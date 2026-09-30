# Learning notes

| Module | Problem and core idea | Choice and alternative | Limitation | Interview question and answer direction |
| --- | --- | --- | --- | --- |
| Go API | Keep public business operations separate from model work. It validates input, persists metadata and forwards internal tasks. | Standard `net/http` plus pgx; a framework could reduce boilerplate. | Synchronous ingestion ties up a request. | **Why split Go and Python?** Go owns the business API; Python has mature parsing/model tooling. State that the network boundary adds latency and failure modes. |
| Migration/data model | Keep knowledge bases, documents and chunks durable and related. | Versioned SQL and foreign keys; ORM migrations are another option. | Migration runner handles only forward SQL. | **How do you prevent partial indexing?** Chunk replacement and `ready` status commit together in one transaction. |
| Parsing | Extract text from PDF/DOCX/TXT/Markdown before indexing. | pypdf/python-docx plus UTF-8 reads; OCR is an alternative for scans. | Scanned PDFs fail clearly; tables/layout may lose semantics. | **What is a common PDF failure?** A scanned page has images but no extractable text; use OCR in a later phase. |
| Chunking | Balance context completeness with retrieval precision. | Paragraph/sentence boundaries, size cap and overlap; token-aware semantic splitters are alternatives. | Character count is not a token budget. | **Why overlap?** It preserves context across boundaries, at the cost of duplicate storage and possible duplicate retrieval. |
| Embedding | Map text/question to comparable vectors. | Provider interface; deterministic hash for tests, local BGE-M3 or an optional cloud endpoint for real vectors. | A provider switch requires reindexing; the small synthetic corpus cannot prove broad semantic quality. | **Can you compare embeddings from different models?** No, their vector spaces generally differ even if dimensions match. |
| Retrieval | Find relevant stored chunks within one knowledge base. | pgvector exact cosine Top-8 with a DB-side KB filter. | Exact search slows with large corpora. | **Where must tenant filtering happen?** In SQL before Top-K, otherwise another KB can leak or displace results. |
| Rerank | Reorder candidates after broad vector retrieval. | Lexical overlap baseline; cross-encoder could improve it later. | Lexical overlap misses paraphrases. | **Why retrieve then rerank?** The first stage narrows the corpus cheaply; the second spends more work on few candidates. |
| Answer/citation | Ground output and show provenance. | Provider returns context indices; server loads citation fields from retrieved rows. | A genuine citation may still fail to support a real model's assertion. | **How do you stop fake citations?** Never trust model-generated IDs/names; validate indices and construct citations from DB rows. |
| Docker | Reproduce the stack with one command. | Compose plus shared upload volume. | Single host only, no HA or background queue. | **What happens if AI service is down?** Go reports a failed document status or chat error; Phase 1 has no retry worker. |

## A real integration lesson

The first end-to-end upload failed although the Go process had saved the file: it used mode `0600`, while the Python container ran as another user. Both containers now share UID/GID 10001, Go writes `0640`, and Python mounts the upload volume read-only. The first citation integration test also revealed a Python UUID object where a string was expected. These are examples of failures that unit tests alone did not expose; the full Docker stack test caught them.

## Phase 2 interview notes

| Topic | Why it matters | Current implementation and tradeoff | Interview answer direction |
| --- | --- | --- | --- |
| Batched embeddings | One network request per chunk wastes latency and rate-limit budget. | The adapter batches up to 32 by default and restores response order by index; the local BGE-M3 profile limits batches to 16. A large document still runs batches sequentially to bound load. | Explain that batch size is a concurrency/cost control. The five actual corpus ingestion calls used 3–4 texts each; these are latency observations, not a throughput limit. |
| Provider failures | 429 and transient 5xx errors are expected external dependencies. | Bounded retry, short backoff, Retry-After cap and per-attempt timeout; 4xx auth errors fail immediately. Errors omit key, headers and response body. | Distinguish retryable transient errors from user/configuration errors, and mention total retry budget. |
| Vector space identity | Equal vector dimensions do not mean vectors from two models are comparable. | PostgreSQL stores provider, model identity and dimension; ingestion/query reject a mismatch. | A model switch requires rebuilding all vectors, even if both outputs are 1536-dimensional. |
| Reindex safety | Updating the active vector column one document at a time mixes spaces and can break retrieval. | Maintenance command writes a separate table, checks all ready documents, atomically swaps tables/profile, keeps a rollback backup. | Explain staging, failure before cutover, cutover transaction, verification and rollback. This is maintenance, not zero downtime. |
| Evaluation | A successful request is not evidence of retrieval or answer quality. | Twenty source-backed questions plus four unsupported questions, raw per-case outputs and explicit Hit@K/Recall@K/MRR/citation metrics. | State the corpus size and synthetic nature; never present the development hash result as real-model quality. |
| Abstention | A citation can be real but support an irrelevant answer. | A baseline run exposed four false answers; a limited development lexical evidence gate made all four abstain on this corpus. | Describe the before/after and note that paraphrases or unseen questions can still fail. |
| Debug privacy | RAG needs visibility into retrieved chunks and latency, but ordinary logs should not become a document archive. | Local debug response includes query/context details; server log records IDs, counts, lengths and time only. | Explain why access control and debug disabling are required before shared deployment. |

## Phase 2.5 interview notes: local real models

| Topic | What the real run taught | Interview answer direction |
| --- | --- | --- |
| Local provider reuse | Ollama's OpenAI-compatible endpoint accepted the existing batch embedding and JSON chat adapters. BGE-M3 returned 1024 dimensions; Qwen2.5 7B Q4_K_M generated actual answers on the RTX 4080. The default hash/extractive mode was left intact. | State the exact models, runtime, GPU and measured vector shape. Separate local inference from untested cloud APIs. |
| GPU and latency | Both models reported 100% GPU placement. The warm final RAG median was 371.25 ms, while one cold Qwen smoke call took 28.5 seconds. | Never quote warm latency as startup latency or single-request latency as concurrent capacity. Explain model loading and cache residency. |
| Safe dimension switch | The main DB was empty, so its column/profile changed atomically from 1536 to 1024 with no vectors to rebuild. A separate nonempty temporary database actually re-embedded four chunks with BGE-M3 and rolled back successfully. | Explain both cases separately. Do not claim the empty main DB had vectors rebuilt. |
| Structural citation vs support | Final evaluation had 21/21 citations matching in-scope DB rows. In a ten-case answer review, P06 still included an adjacent chunk that did not contain the 90-day fact. | A valid row ID proves provenance, not that each cited passage entails the answer. Describe the observed extra citation and the need for stronger support evaluation. |
| Prompt adjustment | Initial local runs showed extra adjacent citations in P01, P06 and P15. A narrower citation instruction was tried with the same corpus and retrieval settings; one over-citation remained in the final fixed-seed run. | Treat before/after as observations, not proof of causality: the earlier runs had no fixed seed and model output can vary. Do not edit evaluation cases to improve scores. |
| Small evaluation set | Hit@8, Recall@8 and MRR were all 1.00 on 20 direct fact questions, just as in development mode. Four unsupported questions abstained. | This demonstrates this particular corpus path, not general model superiority. Propose diverse paraphrases, conflicting policies, longer documents and independent human annotation before a broader claim. |

## Phase 3 interview learning notes

### PostgreSQL Job queue and Go concurrency

1. **Problem:** A synchronous upload holds an HTTP request during parsing, embedding and database writes; a process crash can leave work unfinished.
2. **Core idea:** Commit a Job with the document, then let two goroutines claim due rows through `FOR UPDATE SKIP LOCKED`. A short lease plus heartbeat distinguishes a live slow worker from a dead one.
3. **Why this choice:** Document and Job creation share one PostgreSQL transaction. At this scale, operating another broker adds failure modes without demonstrated need.
4. **Alternatives:** Kafka/RabbitMQ, an external managed queue, or synchronous processing. A broker may be justified at much larger volume or for cross-service event streaming.
5. **Drawbacks:** Polling adds up to about 500 ms wait, a shared DB can become the bottleneck, and retries can repeat an embedding call. File save and DB commit are not atomic together.
6. **Likely interview question:** “Does `SKIP LOCKED` guarantee exactly once?”
7. **Answer:** No. It prevents concurrent claims while rows are locked, but a crash after work and before completion can replay it. A unique Job per document, advisory lock and transactional chunk replacement make the stored result idempotent. The real restart test ended with 3 unique chunks after attempt 2.

### JWT and knowledge-base RBAC

1. **Problem:** An untrusted browser must not use a KB ID in a URL to read or alter another user's documents.
2. **Core idea:** The Go API verifies the signed token, resolves user identity and checks a KB membership row for every route. The Agent tool checks the same boundary again before DB access.
3. **Why this choice:** KB-local `admin/editor/viewer` roles fit a small internal prototype; bcrypt protects stored passwords, and fixed-algorithm short-lived JWTs avoid a server-side session table for now.
4. **Alternatives:** Cookie sessions, OIDC/enterprise SSO, policy engines or row-level security. They bring different revocation and operational tradeoffs.
5. **Drawbacks:** Open registration, no MFA/rate limit/refresh or explicit token revocation; the first creator becomes the admin of a new KB. Existing unowned KBs need a one-time membership assignment.
6. **Likely interview question:** “Why is hiding the Upload button insufficient?”
7. **Answer:** A client can call the API directly. The server denies viewer upload with 403, editor delete with 403, and unassigned KB access with 404. Query SQL also filters by KB before Top-K, and citations come only from authorized results.

### Agent tool calling and prompt injection

1. **Problem:** The model needs a controlled way to request knowledge search or document status without gaining arbitrary system access.
2. **Core idea:** A replaceable planner returns JSON for a two-tool allowlist; code validates exact arguments and permissions, executes one read-only tool, then the answer provider uses its result as lower-trust context. Citation indices resolve to real DB rows.
3. **Why this choice:** The local Qwen OpenAI-compatible endpoint reliably supports structured JSON planning in this test; it was not claimed as native function calling. Restricting tools keeps the security boundary inspectable.
4. **Alternatives:** Native function calling on a verified model, deterministic routing for specific intents, or multi-step planners. Each would need its own measured reliability and tests.
5. **Drawbacks:** One tool per request, poor plan risk, no greeting response without a source, and no proof that every cited passage supports every answer claim. Initial metadata questions abstained until the tool included the requested document UUID.
6. **Likely interview question:** “How do you prevent an uploaded document from making the Agent run `curl` or SQL?”
7. **Answer:** Document text is data in the lower-priority context; the planner can only name two allowlisted tools. Exact argument validation and a database membership check happen at tool execution. Eight injected plans and real cross-KB cases were tested. This is defense for those paths, not a claim of complete prompt-injection resistance.

### Metrics and performance interpretation

1. **Problem:** A correct response hides latency, retries and queue buildup.
2. **Core idea:** Expose HTTP, RAG, retrieval, embedding, LLM, Job and tool counters/durations in Prometheus text. Keep request text and secrets out of ordinary logs.
3. **Why this choice:** A small scrape endpoint makes the critical stages observable without adding a Grafana deployment in this phase.
4. **Alternatives:** OpenTelemetry traces and histograms, persistent Prometheus/Grafana, database query tracing.
5. **Drawbacks:** Counters reset on restart and there is no historical store or alerting. A 24-case local latency comparison can be confounded by GPU activity.
6. **Likely interview question:** “Did Phase 3 make RAG three times slower?”
7. **Answer:** The Phase 3 evaluation bypassed the new Go code. Median generation time rose from roughly 351 to 922 ms while retrieval stayed near 10–12 ms and token counts barely changed. GPU use was high outside requests, but attribution was unavailable. I would repeat under controlled idle-GPU conditions before claiming a causal regression.
