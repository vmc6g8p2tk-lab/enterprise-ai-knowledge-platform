package httpapi

import (
	"fmt"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/VMC6G8P2TK-Lab/enterprise-ai-knowledge-platform/backend/internal/jobs"
)

type responseRecorder struct {
	http.ResponseWriter
	status int
}

func (r *responseRecorder) WriteHeader(status int) {
	r.status = status
	r.ResponseWriter.WriteHeader(status)
}
func (r *responseRecorder) Write(data []byte) (int, error) {
	if r.status == 0 {
		r.status = 200
	}
	return r.ResponseWriter.Write(data)
}

type httpSample struct {
	count   uint64
	seconds float64
}

var safeRoute = regexp.MustCompile(`^[/a-zA-Z0-9{}._-]+$`)
var knownRoutes = map[string]bool{
	"/healthz": true, "/metrics": true, "/auth/register": true, "/auth/login": true,
	"/knowledge-bases": true, "/knowledge-bases/{id}": true,
	"/knowledge-bases/{id}/members": true, "/knowledge-bases/{id}/members/{id}": true,
	"/knowledge-bases/{id}/documents": true, "/knowledge-bases/{id}/documents/{id}": true,
	"/knowledge-bases/{id}/chat": true, "/knowledge-bases/{id}/agent": true,
}

type httpMetrics struct {
	mu      sync.Mutex
	samples map[string]httpSample
}

func newHTTPMetrics() *httpMetrics { return &httpMetrics{samples: make(map[string]httpSample)} }

func routeLabel(path string) string {
	parts := strings.Split(path, "/")
	for index, part := range parts {
		if validUUID(part) {
			parts[index] = "{id}"
		}
	}
	value := strings.Join(parts, "/")
	if len(value) > 160 || !safeRoute.MatchString(value) || !knownRoutes[value] {
		return "other"
	}
	return value
}

func (m *httpMetrics) observe(method, path string, status int, elapsed time.Duration) {
	key := fmt.Sprintf("method=\"%s\",route=\"%s\",status=\"%d\"", method, routeLabel(path), status)
	m.mu.Lock()
	sample := m.samples[key]
	sample.count++
	sample.seconds += elapsed.Seconds()
	m.samples[key] = sample
	m.mu.Unlock()
}

func (a *API) metrics(w http.ResponseWriter, r *http.Request) {
	var pending int
	if err := a.db.QueryRow(r.Context(), "SELECT count(*) FROM processing_jobs WHERE status='pending'").Scan(&pending); err != nil {
		writeError(w, 503, "database unavailable")
		return
	}
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	fmt.Fprintln(w, "# TYPE job_queue_pending gauge")
	fmt.Fprintf(w, "job_queue_pending %d\n", pending)
	fmt.Fprintln(w, "# TYPE job_failures_total counter")
	fmt.Fprintf(w, "job_failures_total %d\n", jobs.Failures())
	a.metricsData.mu.Lock()
	defer a.metricsData.mu.Unlock()
	keys := make([]string, 0, len(a.metricsData.samples))
	for key := range a.metricsData.samples {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	fmt.Fprintln(w, "# TYPE http_requests_total counter")
	fmt.Fprintln(w, "# TYPE http_request_duration_seconds summary")
	for _, key := range keys {
		sample := a.metricsData.samples[key]
		fmt.Fprintf(w, "http_requests_total{%s} %d\n", key, sample.count)
		fmt.Fprintf(w, "http_request_duration_seconds_count{%s} %d\n", key, sample.count)
		fmt.Fprintf(w, "http_request_duration_seconds_sum{%s} %.6f\n", key, sample.seconds)
	}
}
