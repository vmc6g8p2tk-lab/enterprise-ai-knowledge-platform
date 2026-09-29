# Architecture — Phase 1

## Scope

The system provides a minimal document-to-answer RAG loop. This phase has no users, RBAC, background jobs, Redis, agent tools, monitoring, or production rate limits. The default model providers are deterministic development implementations, not neural embeddings or an LLM.

```mermaid
flowchart LR
    Browser[React browser] -->|REST| Go[Go API]
    Go -->|metadata and migrations| PG[(PostgreSQL + pgvector)]
    Go -->|stored original| Files[(Docker uploads volume)]
    Go -->|internal HTTP| AI[Python FastAPI RAG service]
    AI -->|read original| Files
    AI -->|chunks and vectors; scoped retrieval| PG
    AI --> Emb[Embedding provider]
    AI --> Rank[Lexical reranker]
    AI --> Answer[Answer provider]
```

## Responsibilities

- **Go API:** public knowledge base and document endpoints, upload validation, metadata persistence, forwarding RAG requests, migration on startup. SQL parameters prevent injection. The default CORS origin is the local frontend.
- **Python AI service:** file parsing, whitespace cleaning, paragraph-aware chunks with overlap, embeddings, pgvector search, reranking, context assembly, answer generation and server-built citations. Only Docker-internal endpoints are exposed.
- **PostgreSQL/pgvector:** durable metadata and vector rows. A regular SQL migration creates the extension and tables. Each chunk has its parent document, ordered index, content, JSON metadata and 1536-dimensional vector.
- **Docker volume:** retains original uploads so processing survives request completion. The Go and Python containers use a shared UID/GID (10001); Go writes files as `0640`, and Python mounts the volume read-only. The local host API port is 18080 to coexist with another project.
- **React:** small UI for creating/selecting knowledge bases, uploading documents, seeing status, asking questions and seeing citations.

## Ingestion flow

1. Go validates a 10 MiB maximum upload and a supported extension, writes it to a generated path, inserts a `pending` document row.
2. Go calls the Python service synchronously. Python changes status to `processing`, reads the saved file, extracts text, chunks and embeds it.
3. A database transaction replaces this document's chunks and sets `ready` plus count. A processing exception records `failed` plus a bounded reason.
4. Synchronous processing is intentionally simple for Phase 1. A long model call occupies a request; background jobs and retries belong to a later phase.

## Query flow

1. Go validates the knowledge base and question.
2. Python embeds the question, executes a parameterized pgvector cosine-distance search, joining through `documents` and filtering by the requested knowledge base and `ready` status.
3. The top 8 vector results are recorded, lexically reranked, and the best 4 become context.
4. The answer provider returns text plus indices into that context. Python validates indices and constructs citations from the actual database rows, never from model-provided document names or IDs.

## Data integrity and limitations

The migration uses foreign keys and unique `(document_id, chunk_index)` to keep chunks tied to one document. One processing transaction writes all chunks and the final status. Upload file creation and the initial document insert are not one atomic operation, so a process crash can leave an orphan file or `pending` row. There is no authentication yet. The development hash vectors support repeatable tests but do not provide semantic understanding. The OpenAI-compatible providers are optional and need a user-supplied key and separate real-model verification.
