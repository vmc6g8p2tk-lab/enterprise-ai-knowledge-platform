# Enterprise AI Knowledge Platform

A Phase 1 enterprise knowledge base prototype with a Go business API, Python RAG service, React UI, PostgreSQL and pgvector. It is built to make the document-to-answer data path inspectable and testable. It is not a production multi-tenant system.

## Phase 1 features

- Create, list and inspect knowledge bases.
- Upload PDF, DOCX, TXT and Markdown files (10 MiB maximum); store originals and document processing status.
- Extract text, create paragraph-aware overlapping chunks, persist 1536-dimensional vectors and metadata.
- Search within one knowledge base using pgvector cosine distance, rerank a Top-8 candidate set to 4 chunks, and return an answer with database-backed citations.
- Browse knowledge bases, document status and cited answers in a basic React UI.

**Model honesty:** The default `hash` embedding is a deterministic lexical test implementation, and `extractive` answers copy a relevant sentence. Neither is a real semantic model or LLM. Optional OpenAI-compatible providers are wired behind interfaces, but require your own API key and separate end-to-end verification. The UI identifies its development mode.

## Run locally

Install Docker Desktop with a running Engine, then from this repository root:

```sh
docker compose -f deployments/compose.yaml up --build
```

Open `http://localhost:5173`. The Go API listens at host `http://localhost:18080` (container port 8080), PostgreSQL is mapped to host port `55432`, and the Python service is internal to Compose. Host ports bind to `127.0.0.1` for local development; the API port avoids clashing with an existing local project. Check containers with `docker compose -f deployments/compose.yaml ps`. Default Compose credentials are **local development only**. For a shared environment, copy `.env.example` to `.env`, choose a strong `POSTGRES_PASSWORD`, update `DATABASE_URL` to match, and keep `.env` private.

The app starts in test/development model mode. To try real OpenAI-compatible APIs, set `EMBEDDING_PROVIDER=openai`, `ANSWER_PROVIDER=openai`, and `OPENAI_API_KEY` in an untracked `.env`, then **reindex documents** stored under a different embedding provider. The schema expects 1536 dimensions. API cost and output quality have not been measured.

Example API calls:

```sh
curl -X POST http://localhost:18080/knowledge-bases -H "Content-Type: application/json" -d '{"name":"Company handbook"}'
curl -F "file=@handbook.txt" http://localhost:18080/knowledge-bases/KNOWLEDGE_BASE_ID/documents
curl -X POST http://localhost:18080/knowledge-bases/KNOWLEDGE_BASE_ID/chat -H "Content-Type: application/json" -d '{"question":"What is the leave policy?"}'
```

## Architecture

```mermaid
flowchart LR
    UI[React] --> API[Go API]
    API --> DB[(PostgreSQL + pgvector)]
    API --> Volume[(Original uploads)]
    API --> RAG[Python FastAPI RAG]
    RAG --> Volume
    RAG --> DB
    RAG --> Providers[Embedding and answer providers]
```

The Go API owns knowledge base/document metadata, validation and public HTTP routes. The Python service parses saved documents, builds chunks/vectors, performs filtered vector retrieval and creates citations from actual retrieved database rows. See [ARCHITECTURE.md](ARCHITECTURE.md) and [RAG design](docs/RAG_DESIGN.md).

## Tests

The test commands and their **actual outcomes** are recorded in [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md). The Python integration test needs a running Compose PostgreSQL and its migration. It is intentionally not silently skipped if `TEST_DATABASE_URL` is missing.

## Known Phase 1 limits

- No login, RBAC, tenant security, asynchronous task queue, Redis, agent tools, metrics or production deployment controls.
- Ingestion is synchronous and a crash may leave a `pending` row or orphan original file. No automatic retry worker exists.
- Exact pgvector search is suitable for small data sets; ANN index, recall and latency need measurement before a larger deployment.
- PDF extraction does not perform OCR. Document table/layout and citations may be imperfect.
- Hash vectors and lexical reranking do not establish semantic retrieval quality. A genuine citation proves provenance, not answer faithfulness.
- No performance or model-quality benchmark is claimed for this phase.
