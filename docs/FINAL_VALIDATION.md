# Phase 4 final local validation — 2026-09-30

This record separates implemented behavior, automated tests, actual local runs and unverified claims. It is a release-preparation record, not a production certification. No GitHub repository was created and no push was attempted.

## Implemented

- Existing Go/Python/React/PostgreSQL/pgvector architecture, durable two-worker PostgreSQL Job queue, JWT and KB RBAC, local BGE-M3/Qwen providers, two-tool JSON-planned Agent and Prometheus text metrics remain intact.
- A general answer prompt clarification asks the LLM to preserve the source's units and conditions for quantities, times and money. No question-specific `30 minutes` rewrite was added. The unchanged evaluation corpus and retrieval parameters remain committed.
- A fixture-specific citation relevance oracle detects passages without the expected reference phrase; it is explicitly separate from DB existence/scope checks and is not a semantic entailment model. Raw extra citations are retained.
- `.gitignore` additionally excludes database directories/dumps and model caches. README, learning notes and 32 code-linked interview questions were prepared.

## Automated tested

| Check | Final observed result |
| --- | --- |
| Python suite | `34 passed` in `16.32s`, no skips. Two warnings came from pytest attempting to write its cache under a deliberately read-only test mount. Tests included parsing, chunking, provider behavior, prompt contract, fixture relevance oracle, real pgvector, live Compose upload/chat/RBAC/Agent and reindex. |
| Go tests | `go test -count=1 -v ./...` passed in Go 1.24 Alpine against the real `knowledge_test` PostgreSQL database. `TestTwoWorkersClaimDifferentJobs` and `TestRetryLimitAndLeaseRecovery` both ran and passed; they were not skipped. |
| Go vet / race | `go vet ./...` exited 0; `CGO_ENABLED=1 go test -race -count=1 -v ./...` passed with the same real Worker integration tests. The detector covers executed tests, not arbitrary concurrent production traffic. |
| Frontend | `npm run build` passed TypeScript and Vite; Vite v6.4.3 built 29 modules. |
| Compose health | PostgreSQL/pgvector, Go backend, Python AI service, React frontend and Ollama all reported `healthy` with the local-model overlay. |

The first Python run mounted only `ai-service/`, so tests looking for `docs/evaluation` and `backend/migrations` failed: **3 failed, 31 passed**. Mounting the repository root read-only at `/workspace` fixed this environment error; the full second run had 34 passes. An initial Go command mistakenly derived its test URL from a `.env` containing only secrets; the Worker tests skipped. That pass was rejected as evidence and Go was rerun with the explicit dedicated test DB. A first standalone Agent script attempt mounted it at the wrong depth and exited with `IndexError`; the corrected run completed. These failures are not counted as passes.

## Actually run locally

### Model, retrieval and sequential latency

- RTX 4080, 16,376 MiB reported VRAM; Windows Docker Desktop, PostgreSQL/pgvector and Ollama. `ollama list` showed `bge-m3:567m-fp16` and `qwen2.5:7b-instruct-q4_K_M`. The embedding column/profile is 1024-dimensional for this local-model DB.
- Fixed Top-8, rerank-4, 3200 context characters, existing chunks/corpus, temperature 0 and seed 42. One warm-up RAG and one warm-up Agent request were excluded. [Raw 30 ordinary RAG and 20 Agent calls](evaluation/results/phase4_latency.json) were sequential; percentiles interpolate the sorted observed values. The 30 RAG questions cycle the unchanged 20+4 fixture, while the 20 Agent calls cycle four fixed successful questions.

| Stage | 30 RAG median / P95 / P99 | 20 Agent median / P95 / P99 |
| --- | ---: | ---: |
| Embedding | 12.06 / 34.96 / 45.47 ms | 12.02 / 19.45 / 21.91 ms |
| DB retrieval | 9.89 / 11.93 / 14.22 ms | 1.78 / 2.30 / 2.33 ms |
| Lexical rerank | 0.17 / 0.44 / 0.64 ms | 0.17 / 0.26 / 0.60 ms |
| Final answer LLM | 422.96 / 535.34 / 566.69 ms | 316.85 / 388.78 / 399.04 ms |
| Total service | **448.14 / 557.73 / 593.43 ms** | **987.85 / 1096.51 / 1098.29 ms** |

Agent metadata requests skip vector embedding/retrieval; their zeros are included in the Agent stage summaries. Agent total also includes a JSON planner call. RAG wall-clock median/P95/P99 was 450.53/560.38/596.26 ms, Agent wall-clock 991.33/1098.58/1100.85 ms. [Fourteen host samples](evaluation/results/phase4_host_samples.csv) measured 9–93% whole-GPU utilization, 2342–8080 MiB whole-GPU used VRAM and 17.40–61.66% host CPU. They cannot isolate Ollama's usage. The 30 RAG calls reported 1148 completion tokens divided by 13.08 s summed answer-generation stage wall time, about 87.74 tokens per stage-wall second, not pure decode speed. No concurrency limit or sustained throughput was tested.

Phase 2.5 recorded RAG median/P95 371.25/483.56 ms; Phase 3 recorded 948.05/1396.82 ms in a busier, unattributed GPU state. Phase 4 is closer to Phase 2.5. The prompt was also clarified before this Phase 4 run. Different environment/model state and prompt mean these are **historical observations, not an identical-condition A/B benchmark**; no causal optimization is claimed. Retrieval/chunk/model settings were not changed to beautify results.

### Answer quality and citation relevance

The Phase 3 incident Agent answer was exactly `30` although its selected database chunk said `within 30 minutes of discovery`. Source retrieval, context assembly and DB citation were correct; the omission was in model output. The general prompt clarification and mock prompt-contract test were followed by an unchanged real-model incident case. The [full 20+4 rerun](evaluation/results/phase4_rag_real.json) answered `within 30 minutes`; the sequential check's one RAG and five Agent incident cases also included the number and unit. This is case-level regression evidence, not a guarantee for all quantities.

The full 20+4 run retained the same corpus SHA-256 as Phase 3: Hit@8, Recall@8 and MRR each **1.00**; 20/20 positive answers had a citation to the expected document and a citation containing the expected evidence phrase; 24/24 citations matched real rows in the current KB; 4/4 unsupported questions abstained. A lexical expected-phrase check flagged **4/24** citations. Manual passage inspection found two overlapping chunks that still expressed the correct durations in different words, one irrelevant annual-leave extra for P02, and one adjacent incident-reporting extra that did not directly support P06's 90-day access-review frequency. The 30-call repeat flagged 6/32 by that same limited lexical rule. Thus citation provenance and at least one supporting citation passed these fixture checks, while **not every returned citation was relevant**.

### Security, jobs, metrics and logs

- The final Python suite exercised anonymous denial, viewer upload denial, editor KB delete denial, URL KB substitution, cross-KB RAG/Agent scope, eight invalid or injected plans, real job retry and live upload processing. These cases passed in the full suite; see [SECURITY.md](SECURITY.md) and [ASYNC_JOBS.md](ASYNC_JOBS.md) for scope.
- A fresh [12-case local-model Agent run](evaluation/results/phase4_agent_security.json) produced ten expected HTTP 200 responses, one outsider 404 and one cross-KB injected request 403. Its nine returned citations were checked against in-scope database rows; an injected document triggered only allowlisted search. The incident answer included `30 minutes`.
- The sequential run uploaded five real public fixture documents, observed their asynchronous transition to `ready` and their nonzero chunk counts, then queried both RAG and Agent. The earlier real restart/two-worker/retry evidence remains in [PHASE3_VALIDATION.md](PHASE3_VALIDATION.md); the Phase 4 suite reran the retry and authorization cases but did not repeat the manual backend-restart experiment.
- Live Go `/metrics` showed HTTP and queue series; live Python `/metrics` showed RAG, tool and LLM series. These are in-memory counters, not a historical monitoring deployment.
- A scan of 144 current backend/AI-service log lines found no checked Authorization/Bearer headers, password or secret assignments, configured secret values, or full public-fixture body markers. This is a bounded scan of the observed logs, not an all-path privacy proof.

## Repository release scan

- All tracked files and the nine pre-Phase-4 Git commits were scanned for key/token/private-key signatures, Windows home paths and private-IP literals. No matching secret signature or risky historical filename was found. The tracked `.env.example` contains placeholders; no `.env` was tracked. No tracked file exceeded 5 MiB. The final new commits are scanned again before release preparation is reported complete.
- `.env`, uploads, model files/cache and database-data/dump paths match `.gitignore`. Raw JSON/CSV evidence contains only public synthetic fixture content and telemetry, not private uploads.
- README distinguishes test providers, locally verified models and unverified cloud calls; latency numbers identify workstation, request count, warmup and sequential conditions. It makes no production capacity or security certification claim.

## Not validated / known limits

No production traffic, sustained concurrency, cloud-provider run, independent security audit, broad independently annotated answer-quality set, pure Ollama GPU attribution, native function calling or multi-host HA was validated. Two extra citations remained weak/irrelevant in the 20+4 run. A prompt change can reduce the observed unit-loss case but cannot guarantee every future answer preserves units. The current auth has no email verification, login rate limit or explicit JWT revocation. Do not describe this local prototype as production-ready.
