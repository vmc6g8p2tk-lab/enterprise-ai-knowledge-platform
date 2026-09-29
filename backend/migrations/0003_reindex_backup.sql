-- Keep the previous vector-space identity during a staged reindex cutover.
CREATE TABLE embedding_profile_backup (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    provider_name text NOT NULL,
    model_id text NOT NULL,
    dimensions integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
