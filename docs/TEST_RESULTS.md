# Test results

## Phase 2 final verification (2026-09-30)

These results are from the existing local Docker Desktop project. PostgreSQL/pgvector and the four Compose services were real; embedding/answer providers were the deterministic development implementations. No `OPENAI_API_KEY` was present, so live embedding, live LLM, model latency, cost and semantic answer quality are **not verified**.

| Check | Observed result |
| --- | --- |
| Python full suite | `18 passed in 2.95s` with `TEST_DATABASE_URL` and `E2E_API_URL` set after the final AI image rebuild; includes real pgvector integration, provider mock/error/timeout/batch tests, knowledge-base isolation, citation regression, evaluation runner and staged reindex/rollback/finalize in an isolated real database |
| Go tests | `go test -count=1 ./...` PASS in `golang:1.24-alpine`; `internal/httpapi` passed, other Go packages have no tests |
| Go vet | `go vet ./...` PASS in the same Linux container |
| Go race | `CGO_ENABLED=1 go test -race -count=1 ./...` PASS with GCC/musl in the Linux container; this covers the existing Go tests, not a concurrent load |
| Frontend | `npm run build` PASS; TypeScript and Vite build completed |
| Compose | PostgreSQL, Go backend, Python AI service and frontend all reported `healthy` after rebuild |
| Migrations | Real PostgreSQL `schema_migrations` contains `0001_initial.sql`, `0002_embedding_profile.sql`, `0003_reindex_backup.sql` |
| Local evaluation before/after | 20 source-backed positive and 4 unsupported questions, same corpus/config; Hit@8, Recall@8 and MRR all 1.00 in both runs; exact citation validity and KB scope 1.00; unsupported refusal changed 0/4 to 4/4. Raw case-level data in `docs/evaluation/results/` |
| Real model calls | NOT TESTED; no user API key was supplied |

The first Phase 2 integration test found a `dict_row`/tuple access error in the embedding-profile guard; it was corrected and the real database test passed afterward. The initial local evaluation exposed 4/4 unsupported-question refusal failures in the development answerer. Its evidence gate was changed and rerun; the failures remain recorded in the baseline JSON and `RAG_EVALUATION.md`. A plain host Go invocation also failed because `go` was not on PATH, and a direct invocation failed because the default Windows Go cache was inaccessible; the requested Go checks were successfully executed in the Linux container. These failed attempts are not counted as passes.

## Phase 1 historical verification

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
