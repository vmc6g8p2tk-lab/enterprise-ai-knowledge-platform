# Test results

This file records executed commands and observed outcomes on a Windows Docker Desktop machine. No skipped or unrun test is counted as passed. The default hash embedding and extractive answer mode were used throughout; no real model key was supplied.

| Check | Result | Evidence |
| --- | --- | --- |
| Go unit tests | PASS | `go test -count=1 ./...`: `internal/httpapi` passed; other Go packages have no tests |
| Go vet | PASS | `go vet ./...`: exit 0 |
| Go race detector | PASS (Linux container) | `CGO_ENABLED=1 go test -race -count=1 ./...` in a temporary `golang:1.24-alpine` container with GCC/musl: `internal/httpapi` passed; other packages have no tests |
| Python parser/chunk/provider unit tests | PASS | `tests/test_unit.py`: actual PDF/DOCX/TXT/Markdown fixtures, chunk boundaries, test providers and reranker |
| Frontend TypeScript/build | PASS | `npm run build`: TypeScript and Vite production build exit 0 |
| Frontend dependency audit | PASS | `npm audit`: 0 vulnerabilities after updating Vite to 6.4.3 |
| Docker Compose health | PASS | Final `docker compose -f deployments/compose.yaml ps`: postgres, backend, ai-service, frontend all `running/healthy`; host ports bind only `127.0.0.1` |
| PostgreSQL migration | PASS | `psql`: pgvector extension `vector` exists; `schema_migrations` contains `0001_initial.sql` |
| PostgreSQL/pgvector integration | PASS | Real DB test writes more than one chunk, checks `ready` and count, runs vector search, checks KB isolation and citation row |
| Full API upload/chat/citation | PASS | Real HTTP test creates two KBs, uploads TXT, queries Go API, checks Top-K and rerank trace, compares cited ID/index/content and 1536 dimensions to PostgreSQL, confirms KB isolation |
| Other upload formats/failure | PASS | Real HTTP test uploads generated PDF, DOCX and Markdown; all reached `ready`. A blank TXT reached `failed` with a stored reason |
| Python full suite | PASS | Final `pytest -q tests`: **7 passed in 0.82 s** after final Compose port binding |
| Frontend served | PASS | Host `GET http://localhost:5173/` returned HTTP 200 |
| Real LLM/semantic embedding | NOT TESTED | No user API key; default providers are deterministic development implementations |

## Failures found and corrected

1. The first Compose attempt used the directory-derived project name `deployments`, which overlapped a separate existing project's Compose name. The attempt was stopped, its three new service containers removed from the old project, and the old PostgreSQL container was restored to its original image/configuration. The new Compose file now has the explicit project name `enterprise-ai-knowledge-platform`; its four containers and volumes are isolated. The old project's PostgreSQL was verified healthy afterward.
2. Docker Hub base-image token requests failed with TLS timeout/EOF. The project Dockerfiles now pull official base images through `mirror.gcr.io`; `pgvector/pgvector:pg17` pulled directly. This change affects image distribution, not application behavior.
3. Host port 8080 was already in use by another project. The new Go API binds host port 18080; its container still listens on 8080.
4. The first real integration run had **2 failures**: a direct RAG result exposed a Python UUID object rather than a string, and the AI container could not read Go's `0600` upload file. The service now stringifies citation document IDs; both containers share UID/GID 10001 and Go stores files as `0640` on a Python read-only volume. The unchanged two integration tests then passed.
5. A later expanded test run failed collection due to a test-file indentation typo. The typo was fixed; the final full run had 7 passing tests.

The native Windows `go test -race` attempt could not start because CGO was disabled by default; a second attempt with local GCC failed during cgo compilation. That failed attempt is **not** counted as passed. The same Go race command then passed inside a temporary Linux container with CGO, GCC and musl. Its scope remains the existing Go unit tests; it is not a concurrent load test.
