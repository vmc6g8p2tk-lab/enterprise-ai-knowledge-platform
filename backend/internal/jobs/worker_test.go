package jobs

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

func testPool(t *testing.T) *pgxpool.Pool {
	t.Helper()
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Skip("requires TEST_DATABASE_URL; not counted as integration pass")
	}
	pool, err := pgxpool.New(context.Background(), url)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)
	return pool
}

func testJob(t *testing.T, pool *pgxpool.Pool, label string) (string, string) {
	t.Helper()
	ctx := context.Background()
	id := fmt.Sprintf("%08x-1111-4111-8111-%012x", time.Now().UnixNano()&0xffffffff, time.Now().UnixNano()&0xffffffffffff)
	doc := fmt.Sprintf("%08x-2222-4222-8222-%012x", time.Now().UnixNano()&0xffffffff, time.Now().UnixNano()&0xffffffffffff)
	job := fmt.Sprintf("%08x-3333-4333-8333-%012x", time.Now().UnixNano()&0xffffffff, time.Now().UnixNano()&0xffffffffffff)
	if _, err := pool.Exec(ctx, "INSERT INTO knowledge_bases(id,name) VALUES($1,$2)", id, label); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = pool.Exec(context.Background(), "DELETE FROM knowledge_bases WHERE id=$1", id) })
	if _, err := pool.Exec(ctx, "INSERT INTO documents(id,knowledge_base_id,original_filename,media_type,storage_path) VALUES($1,$2,'test.txt','text/plain','/tmp/test.txt')", doc, id); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, "INSERT INTO processing_jobs(id,document_id) VALUES($1,$2)", job, doc); err != nil {
		t.Fatal(err)
	}
	return job, doc
}

func TestTwoWorkersClaimDifferentJobs(t *testing.T) {
	pool := testPool(t)
	job1, _ := testJob(t, pool, "concurrent worker A")
	job2, _ := testJob(t, pool, "concurrent worker B")
	var active, maxActive, calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		now := active.Add(1)
		for {
			old := maxActive.Load()
			if now <= old || maxActive.CompareAndSwap(old, now) {
				break
			}
		}
		time.Sleep(300 * time.Millisecond)
		active.Add(-1)
		w.WriteHeader(200)
	}))
	defer server.Close()
	var group sync.WaitGroup
	for _, id := range []string{"00000000-0000-4000-8000-00000000000a", "00000000-0000-4000-8000-00000000000b"} {
		group.Add(1)
		go func(id string) {
			defer group.Done()
			w := &Worker{DB: pool, AIURL: server.URL, ID: id, Token: "test", HTTPClient: server.Client()}
			if err := w.Step(context.Background()); err != nil {
				t.Error(err)
			}
		}(id)
	}
	group.Wait()
	if calls.Load() != 2 || maxActive.Load() != 2 {
		t.Fatalf("calls=%d max concurrent=%d", calls.Load(), maxActive.Load())
	}
	for _, job := range []string{job1, job2} {
		var status string
		var attempts int
		if err := pool.QueryRow(context.Background(), "SELECT status,attempt_count FROM processing_jobs WHERE id=$1", job).Scan(&status, &attempts); err != nil {
			t.Fatal(err)
		}
		if status != "completed" || attempts != 1 {
			t.Fatalf("job %s: %s attempts=%d", job, status, attempts)
		}
	}
}

func TestRetryLimitAndLeaseRecovery(t *testing.T) {
	pool := testPool(t)
	job, _ := testJob(t, pool, "retry worker")
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) <= 2 {
			w.WriteHeader(503)
			return
		}
		w.WriteHeader(200)
	}))
	defer server.Close()
	worker := &Worker{DB: pool, AIURL: server.URL, ID: "00000000-0000-4000-8000-00000000000c", Token: "test", HTTPClient: server.Client()}
	for attempt := 1; attempt <= 3; attempt++ {
		if err := worker.Step(context.Background()); err != nil {
			t.Fatal(err)
		}
		var status string
		var count int
		var available time.Time
		if err := pool.QueryRow(context.Background(), "SELECT status,attempt_count,available_at FROM processing_jobs WHERE id=$1", job).Scan(&status, &count, &available); err != nil {
			t.Fatal(err)
		}
		if count != attempt {
			t.Fatalf("attempt=%d count=%d", attempt, count)
		}
		if attempt < 3 {
			if status != "pending" || !available.After(time.Now()) {
				t.Fatalf("retry not delayed: %s", status)
			}
			_, _ = pool.Exec(context.Background(), "UPDATE processing_jobs SET available_at=now() WHERE id=$1", job)
		} else if status != "completed" {
			t.Fatalf("final status=%s", status)
		}
	}
	failedJob, _ := testJob(t, pool, "exhausted worker")
	failServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }))
	defer failServer.Close()
	worker.AIURL = failServer.URL
	for attempt := 1; attempt <= 3; attempt++ {
		if err := worker.Step(context.Background()); err != nil {
			t.Fatal(err)
		}
		_, _ = pool.Exec(context.Background(), "UPDATE processing_jobs SET available_at=now() WHERE id=$1 AND status='pending'", failedJob)
	}
	var status, lastError string
	var attempts int
	if err := pool.QueryRow(context.Background(), "SELECT status,attempt_count,last_error FROM processing_jobs WHERE id=$1", failedJob).Scan(&status, &attempts, &lastError); err != nil {
		t.Fatal(err)
	}
	if status != "failed" || attempts != 3 || !strings.Contains(lastError, "processing failed") {
		t.Fatalf("exhausted: %s %d %q", status, attempts, lastError)
	}
	leaseJob, _ := testJob(t, pool, "restart recovery")
	_, err := pool.Exec(context.Background(), `UPDATE processing_jobs SET status='processing',attempt_count=1,
		worker_id='00000000-0000-4000-8000-000000000001',lease_until=now()-interval '1 second' WHERE id=$1`, leaseJob)
	if err != nil {
		t.Fatal(err)
	}
	worker.AIURL = server.URL
	worker.ID = "00000000-0000-4000-8000-00000000000d"
	if err := worker.Step(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := pool.QueryRow(context.Background(), "SELECT status,attempt_count FROM processing_jobs WHERE id=$1", leaseJob).Scan(&status, &attempts); err != nil {
		t.Fatal(err)
	}
	if status != "completed" || attempts != 2 {
		t.Fatalf("recovery: %s %d", status, attempts)
	}
}
