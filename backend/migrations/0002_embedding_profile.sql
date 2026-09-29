-- A vector space is identified by provider, model and dimension. Mixed spaces are unsafe.
CREATE TABLE embedding_profile (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    provider_name text NOT NULL,
    model_id text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Phase 1 stored only hash-v1 vectors. Existing rows are explicitly adopted.
INSERT INTO embedding_profile(singleton, provider_name, model_id, dimensions)
SELECT true, 'hash', 'hash-v1', 1536
WHERE EXISTS (SELECT 1 FROM document_chunks);
