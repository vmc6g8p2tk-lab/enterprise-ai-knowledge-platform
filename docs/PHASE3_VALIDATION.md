# Phase 3 validation — 2026-09-30, local Docker Desktop

## Implemented and actually validated

| Area | Evidence from this run |
| --- | --- |
| Compose | PostgreSQL/pgvector, Go backend, Python AI service, React frontend and Ollama all reported `healthy` with the local-model overlay. |
| Async upload | Full Compose editor upload returned HTTP 202 in **5.70 ms** in one e2e case; Job then completed in one attempt, with **213.78 ms** queue wait and **90.99 ms** worker processing. The document had multiple real pgvector chunks. These are single-case timings. |
| Two workers | Go integration test used real PostgreSQL and two Worker instances with a controlled HTTP processor; both claims ran concurrently (`maxActive=2`), each Job completed once. The processor in this specific test was controlled, while the full Compose ingestion tests used the real Python/BGE service. |
| Retry | Controlled 503 returned twice then succeeded on attempt 3; permanent 503 ended `failed` after 3. A full Compose blank TXT caused three real parse failures and a terminal Job with `last_error`, timestamps and retained document failure reason. |
| Actual restart recovery | `validate_worker_restart.py prepare`, real `docker compose restart backend`, then `verify` gave `completed`, `attempt_count=2`, document `ready`, `chunk_count=3`, three unique indexes, `duplicate_chunks=false`. |
| RBAC | Anonymous route 401; viewer upload 403; editor KB delete 403; outsider KB GET 404; changed KB ID chat/Agent 404. An editor uploaded and a viewer queried an allowed KB, with DB-verified in-scope citations. |
| Prompt/tool boundaries | Eight parameterized invalid tool plans rejected; another cross-KB document ID denied. Real-model unauthorized user was 404 and injected cross-KB tool request was 403. An injected document did not cause a non-allowlisted tool execution. This is case coverage, not a security proof. |
| Real Agent | [Latest raw 12 cases](evaluation/results/phase3_agent_local_real_verified.json): ten 200, one expected 403, one expected 404; eight returned citations all matched in-scope DB rows, including field-for-field metadata checks. Five fact searches, two metadata answers and one injected-document search returned cited answers. Greeting and unsupported question abstained. |
| Real RAG regression | [Unchanged 20+4 fixture](evaluation/results/phase3_rag_real.json): Hit@8=1.00, Recall@8=1.00, MRR=1.00; 20/20 DB-valid in-scope citations and expected documents; 4/4 unsupported refusals. |
| Metrics | Go `/metrics` exposed HTTP count/duration, `job_queue_pending` (0 after drain) and `job_failures_total` (6 after two blank-file e2e rounds). Python `/metrics` exposed RAG, embedding, retrieval, LLM, Agent and tool counts/durations; `tool_calls_total{tool="search_knowledge_base"}=1` was observed after a live Agent request. In-process counters reset on restart. |

## Automated regression commands and results

With a private `.env` at repository root and the local-model overlay:

```sh
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml up -d --build
docker compose --env-file .env -f deployments/compose.yaml -f deployments/compose.local-model.yaml ps
```

- Python: `TEST_DATABASE_URL` pointed to a separate `knowledge_test` vector(1536) database; `E2E_DATABASE_URL` to the main local-model vector(1024) database; `E2E_API_URL` to the live Go API. `python -m pytest -q tests` from `ai-service/`: **31 passed in 23.49s** on the final run. No test was skipped. The full suite includes real pgvector, parsing, provider errors, reindex, live Compose upload/chat, RBAC and Agent cases.
- Go: `go test -count=1 ./...`, `go vet ./...`, and `CGO_ENABLED=1 go test -race -count=1 ./...` in a Go 1.24 container with the real `knowledge_test` URL: all exited 0. Worker integration tests ran rather than skipping. Race detector passed its exercised paths.
- Frontend: `npm run build` exited 0; TypeScript and Vite build passed.
- Compose: all five services reported healthy after final rebuild.
- Real-model evaluation: `docker compose ... exec -T ai-service python -m app.evaluate --corpus /evaluation --output /tmp/phase3_rag_real.json --top-k 8 --rerank-k 4 --context-chars 3200`; raw JSON copied to `docs/evaluation/results/phase3_rag_real.json`.
- Real Agent cases: `python -m scripts.phase3_agent_validation --output ../docs/evaluation/results/phase3_agent_local_real_verified.json` from `ai-service/` with `E2E_API_URL` and `E2E_DATABASE_URL` set. The script uses only committed public synthetic fixture content, checks chunk and metadata citations against live database rows, then removes its test KBs/users.

## Actual single-request timing and latency investigation

Five public fixture uploads in the latest strict-citation Agent run returned in **4.04–38.11 ms**; queue wait was **163.63–269.10 ms**, and worker time **40.24–111.51 ms**. This includes warm and transient conditions and is not a throughput benchmark. Ten HTTP 200 Agent calls had **3,451.84 ms median wall time**, range **663.20–5,940.53 ms**; the greeting abstained without a tool. Search tool calls took **17.16–28.42 ms**, metadata **0.87–1.29 ms**. The preceding after-fix run measured 2,219.11 ms median over the same ten HTTP 200 cases; this variation reinforces that these are local single-request observations, not stable capacity numbers.

The 24-case Phase 3 RAG function had **948.05 ms median / 1,396.82 ms P95**, above the older Phase 2.5 **371.25/483.56 ms**. Stage comparison placed most added time in LLM generation: median **350.78→921.93 ms**. Median embedding was **11.80→16.49 ms** and DB retrieval **9.88→11.76 ms**. Prompt/completion token counts were almost identical. The evaluation bypasses Go RBAC/Job code, so the new Go path is not the cause. A contemporaneous `nvidia-smi` sample showed about **60% GPU utilization and 12.5 GiB total GPU memory used outside an evaluation request**; process attribution was unavailable. This suggests environment contention but does not prove the source. No unmeasured RAG parameter change was made. A controlled quiet-GPU comparison remains open.

## Failures kept in the record

- An early Python test command from the repository root failed import collection; running from `ai-service/` fixed the test path. The next run without `TEST_DATABASE_URL` failed the real pgvector runner; configuring the dedicated test DB fixed it.
- The first Go compile found a missing `fmt` import. The first Worker test used non-UUID test worker IDs against a UUID column; both were corrected and retested.
- The initial Agent UUID regex omitted a UUID group. Two Agent integration tests caught it. A later test caught a `total`/`total_ms` metric key mismatch. Both are fixed.
- An anonymous full-Compose request exposed a nil-request panic in the HTTP metrics wrapper after writing 401. The wrapper now captures method/path before authentication and has a regression test. The live RBAC test then passed.
- In the first [real Agent run](evaluation/results/phase3_agent_local_real_before.json), two metadata tool calls produced abstentions. The tool result now includes the DB-owned document UUID. Both returned cited answers in the [first after run](evaluation/results/phase3_agent_local_real_after.json) and [strict-citation recheck](evaluation/results/phase3_agent_local_real_verified.json); the cases were unchanged. These runs are not a statistical model-quality estimate.
- In the strict-citation run, the incident-reporting answer was only `30` without the `minutes` unit, although its cited passage contained the full fact. An assistant case review treated this as an incomplete answer. Citation provenance must not be read as complete answer quality.

## Not validated / known limitations

No production auth hardening, cloud provider run, multi-host deployment, HA queue, sustained model concurrency benchmark, independent prompt-injection audit or independent answer-quality annotation was performed. The JSON planner is not native function calling and currently executes at most one read-only tool per request. The greeting case abstains. The newer RAG run's latency needs a quiet-GPU repeat before comparing releases. No claim about production throughput or enterprise security certification follows from these local results.
