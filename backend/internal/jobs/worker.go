package jobs

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const lease = 45 * time.Second

var failureCount atomic.Uint64

func Failures() uint64 { return failureCount.Load() }

type Worker struct {
	DB         *pgxpool.Pool
	AIURL      string
	Token      string
	ID         string
	HTTPClient *http.Client
}

type Job struct {
	ID         string
	DocumentID string
	Attempt    int
	Max        int
	CreatedAt  time.Time
}

// Run keeps the queue durable across process restarts. A lost worker's lease is
// reclaimed, while the document processor's advisory lock serializes processing.
func (w *Worker) Run(ctx context.Context) {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		if err := w.Step(ctx); err != nil && !errors.Is(err, context.Canceled) {
			log.Printf("job worker error: %v", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// Step reclaims expired leases, then atomically claims at most one due job.
func (w *Worker) Step(ctx context.Context) error {
	if _, err := w.DB.Exec(ctx, `
		WITH recovered AS (
			UPDATE processing_jobs SET status=CASE WHEN attempt_count>=max_attempts THEN 'failed' ELSE 'pending' END,
			last_error='worker lease expired', available_at=now(), lease_until=NULL,worker_id=NULL,
			finished_at=CASE WHEN attempt_count>=max_attempts THEN now() ELSE NULL END,updated_at=now()
			WHERE status='processing' AND lease_until<now() RETURNING document_id,status
		)
		UPDATE documents d SET status='failed',failure_reason='worker lease expired',updated_at=now()
		FROM recovered WHERE d.id=recovered.document_id AND recovered.status='failed'`); err != nil {
		return fmt.Errorf("recover expired jobs: %w", err)
	}
	var job Job
	err := w.DB.QueryRow(ctx, `
		WITH candidate AS (
			SELECT id FROM processing_jobs WHERE status='pending' AND available_at<=now()
			ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1
		)
		UPDATE processing_jobs AS j SET status='processing',attempt_count=attempt_count+1,
			worker_id=$1,lease_until=now()+interval '45 seconds',started_at=now(),finished_at=NULL,updated_at=now()
		FROM candidate WHERE j.id=candidate.id
		RETURNING j.id,j.document_id,j.attempt_count,j.max_attempts,j.created_at`, w.ID).
		Scan(&job.ID, &job.DocumentID, &job.Attempt, &job.Max, &job.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("claim job: %w", err)
	}
	// Heartbeats prevent a long embedding request from being mistaken for a dead worker.
	stop := make(chan struct{})
	go func() {
		ticker := time.NewTicker(lease / 3)
		defer ticker.Stop()
		for {
			select {
			case <-stop:
				return
			case <-ctx.Done():
				return
			case <-ticker.C:
				beatCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
				_, _ = w.DB.Exec(beatCtx, `UPDATE processing_jobs SET lease_until=now()+interval '45 seconds',updated_at=now()
					WHERE id=$1 AND worker_id=$2 AND status='processing'`, job.ID, w.ID)
				cancel()
			}
		}
	}()
	started := time.Now()
	processErr := w.process(ctx, job.DocumentID)
	close(stop)
	finalCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if processErr == nil {
		_, err = w.DB.Exec(finalCtx, `UPDATE processing_jobs SET status='completed',lease_until=NULL,worker_id=NULL,
			finished_at=now(),last_error='',updated_at=now() WHERE id=$1 AND worker_id=$2 AND status='processing'`, job.ID, w.ID)
	} else {
		failureCount.Add(1)
		// Bounded backoff: 2, 4, 8 seconds. The final failed state is inspectable.
		backoff := 1 << min(job.Attempt, 3)
		_, err = w.DB.Exec(finalCtx, `WITH changed AS (
			UPDATE processing_jobs SET status=CASE WHEN attempt_count>=max_attempts THEN 'failed' ELSE 'pending' END,
			last_error=$3,available_at=now()+($4 * interval '1 second'),lease_until=NULL,worker_id=NULL,
			finished_at=CASE WHEN attempt_count>=max_attempts THEN now() ELSE NULL END,updated_at=now()
			WHERE id=$1 AND worker_id=$2 AND status='processing' RETURNING document_id,status
		)
		UPDATE documents d SET status=changed.status,
			failure_reason=CASE WHEN d.status='failed' AND d.failure_reason<>'' THEN d.failure_reason ELSE $3 END,
			updated_at=now() FROM changed WHERE d.id=changed.document_id`, job.ID, w.ID, "document processing failed", backoff)
		log.Printf("job %s attempt %d/%d failed after %s: %v", job.ID, job.Attempt, job.Max, time.Since(started), processErr)
	}
	return err
}

func (w *Worker) process(ctx context.Context, documentID string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodPost,
		strings.TrimRight(w.AIURL, "/")+"/internal/documents/"+documentID+"/process", nil)
	if err != nil {
		return err
	}
	request.Header.Set("X-Internal-Token", w.Token)
	response, err := w.HTTPClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 1024))
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("AI service returned %d", response.StatusCode)
	}
	return nil
}
