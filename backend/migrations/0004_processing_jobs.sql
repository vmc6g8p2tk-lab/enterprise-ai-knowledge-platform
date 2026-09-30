CREATE TABLE processing_jobs (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','completed','failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    last_error text NOT NULL DEFAULT '',
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    worker_id uuid,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX processing_jobs_pending_idx ON processing_jobs (available_at, created_at)
    WHERE status = 'pending';
CREATE INDEX processing_jobs_lease_idx ON processing_jobs (lease_until)
    WHERE status = 'processing';
