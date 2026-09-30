# Durable document jobs

## Implemented

`0004_processing_jobs.sql` adds one Job per document (`UNIQUE(document_id)`). Columns include `status`, `attempt_count`, `max_attempts` (default 3), `last_error`, `available_at`, `lease_until`, `worker_id`, `started_at`, `finished_at`, and timestamps. Partial indexes cover due pending jobs and expired processing leases. Job success is `completed`; the older document success state stays `ready` so the RAG and reindex paths remain compatible.

Upload writes the original file, then commits the document and Job inserts together. HTTP 202 means accepted for processing, **not** embedded. Two Go worker goroutines poll every 500 ms. A single SQL CTE locks one due Job with `FOR UPDATE SKIP LOCKED` and changes it to `processing`. The lock lasts only for that short statement; no database transaction is held across the Python/model call. The 45-second lease is extended every 15 seconds. Failures requeue after 2/4 seconds or terminate after the third attempt; a third-attempt delay is not used. An expired lease is reclaimed on the next poll, or marked terminal if the attempt budget is already exhausted.

Python takes a per-document PostgreSQL session advisory lock, so a slow original worker and a reclaimed worker cannot write chunks simultaneously. A chunk transaction deletes old rows, inserts the new set and marks the document `ready`. `(document_id, chunk_index)` enforces uniqueness. This is **at-least-once** processing with idempotent final state, not a claim of exactly-once execution. The external embedding call may be repeated after a crash.

PostgreSQL queue is enough at this scale: it shares the document transaction, retains jobs over process restart and supports concurrent claims without another broker to operate. The queue and vector writes still share one database; large job volumes would need retention, partitioning and resource controls before a broker decision.

## Actually validated

- A real pgvector Go integration test ran two Worker instances concurrently against two jobs and observed two simultaneous processing calls, each job claimed once.
- Controlled 503 responses produced two delayed retries and a successful third attempt. A permanent 503 exhausted three attempts and left `failed`, `last_error`, and timestamps.
- The live Compose blank-document path exhausted three real AI-processing attempts and retained the parse failure reason.
- A real backend container restart recovered an intentionally expired lease: `attempt_count=2`, document `ready`, three unique chunk indexes, no duplicate chunks. The validation script is `ai-service/scripts/validate_worker_restart.py`.
- A direct reprocessing integration test wrote the same document twice and verified the chunk count did not grow.

See [PHASE3_VALIDATION.md](PHASE3_VALIDATION.md) for commands and results. PostgreSQL's [SKIP LOCKED documentation](https://www.postgresql.org/docs/17/sql-select.html) describes its intended queue-like use and its inconsistent view limitation.

## Not validated / limitations

The worker count is fixed at two in this phase. There is no priority, dead-letter re-drive UI, scheduled cancellation, queue retention/archival, or multi-host throughput benchmark. Crashing after saving a file but before committing the document can leave an orphan upload file. The 45-second lease depends on heartbeat; a stalled process that cannot heartbeat can be reclaimed and may duplicate model calls, but document-level locking and transactional replacement prevent duplicate stored chunks.
