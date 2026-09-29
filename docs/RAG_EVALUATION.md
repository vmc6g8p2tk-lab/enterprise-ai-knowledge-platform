# Phase 2 RAG evaluation

## What was measured

The versioned [corpus](evaluation/corpus) contains five synthetic, public enterprise documents. [cases.json](evaluation/cases.json) has 20 questions whose expected reference strings occur in those documents, plus four deliberately unsupported questions. The runner validates every expected reference against the source and then against an actual indexed chunk. It creates a temporary knowledge base in PostgreSQL/pgvector, processes documents with the selected provider, measures results, and removes its rows afterward.

These are **development-provider results**: hash-v1 lexical vectors and the local extractive answerer. They are not semantic model or LLM results, and the test set is small and authored with the corpus. The corpus SHA-256 in both saved runs is 2390d29d5b3dd398e93b5235f5444e60b07dc19b7e53b3295954f157b4b30881.

| Metric | Before | After |
| --- | ---: | ---: |
| Positive / unsupported cases | 20 / 4 | 20 / 4 |
| Retrieval Hit@8 | 1.00 | 1.00 |
| Retrieval Recall@8 | 1.00 | 1.00 |
| Retrieval MRR | 1.00 | 1.00 |
| Returned citation count | 24 | 20 |
| Citation exact DB-row validity | 1.00 | 1.00 |
| Citation in current knowledge base | 1.00 | 1.00 |
| Positive answer cites expected document | 1.00 | 1.00 |
| Positive answer does not abstain | 1.00 | 1.00 |
| Unsupported question correctly abstains | **0.00** | **1.00** |

Evidence: [before JSON](evaluation/results/baseline_hash.json) and [after JSON](evaluation/results/after_evidence_gate_hash.json). Both runs used top_k=8, rerank_k=4, max_context_chars=3200 on the same corpus hash. Per-case output includes question, retrieved/reranked/context chunk IDs, distances, similarity, answer, citation checks and stage latencies. Latencies are observations for this small local run; they are **not** throughput or service capacity benchmarks.

## Diagnosis and one change

Before the change, the development extractive answerer accepted one shared word, including a common function word. It therefore returned unrelated sentences for unsupported questions such as a CEO phone number. One source explicitly states that recording retention is not specified, but the answerer quoted the sentence without abstaining. All four unsupported cases failed.

The change is limited to the development answerer: remove common English function words, require at least two shared substantive terms, and treat an explicitly absent rule as insufficient evidence. All four unsupported cases then abstained with no citation; the 20 positive cases still cited the expected document. The retrieval parameters stayed at 8/4/3200 because the measured retrieval set had no misses to justify tuning them.

This lexical gate is a baseline for tests. It may reject valid paraphrases, and it does not prove factual correctness of a real LLM. We did not score the supplied reference answers for semantic equivalence. Citation validity means the citation matches a database chunk in scope, not that every claim is entailed by that chunk.

## Metric definitions

- **Hit@K:** fraction of positive questions with at least one expected indexed chunk among the first K vector results.
- **Recall@K:** per-question fraction of expected indexed chunks found in the first K, averaged over positive questions. This corpus normally has one expected chunk per case, so Recall@K and Hit@K coincide; a larger set should include more multi-chunk questions.
- **MRR:** mean reciprocal rank of the first expected chunk among vector results.
- **Citation validity:** exact match of chunk ID, document ID, chunk index and content against PostgreSQL.
- **Scope:** the cited chunk's document belongs to the current knowledge base.
- **Expected-document citation:** at least one citation names the case's expected source document.
- **Unsupported refusal:** structured abstained=true and zero citations. A real model can still produce a misleading answer with that flag; human review or a validated judge would be needed for semantic faithfulness.

## Repeat the run

With Compose PostgreSQL and migrations running, use the Python environment in ai-service/ and set DATABASE_URL to the local host port. Then run:

    python -m app.evaluate --output ../docs/evaluation/results/recheck_hash.json --top-k 8 --rerank-k 4 --context-chars 3200

The runner fails rather than skipping if the database, source files or provider are unavailable. A real-provider evaluation needs a user-supplied API key, a compatible embedding profile and reindexed corpus. Keep such results in a separately named artifact; never relabel these hash results as real-model results.

## Phase 2.5: local real-model acceptance (2026-09-30)

This section uses **locally executed neural models**, distinct from the hash/extractive numbers above. Cloud OpenAI or other cloud models remain **unverified**. The machine had an NVIDIA RTX 4080 (16,376 MiB reported VRAM) and Docker Desktop's NVIDIA runtime. Ollama 0.34.4 ran in an opt-in GPU Compose service. Its `ps` output reported both models as `100% GPU`:

| Role | Model / Ollama ID | Format | Measured output/config |
| --- | --- | --- | --- |
| Embedding | `bge-m3:567m-fp16` / `790764642607` | F16, 566.70M parameters | 1024-dimensional vectors confirmed by a real two-text API call and pgvector; 16-text request limit in the adapter |
| Answer | `qwen2.5:7b-instruct-q4_K_M` / `845dbda0ea48` | Q4_K_M, 7.6B parameters | JSON chat completion, `temperature=0`, `seed=42`, Ollama runtime context 4096 |

The existing OpenAI-compatible interfaces call Ollama at `http://ollama:11434/v1` inside Compose. Ollama ignores the non-secret `ollama` key placeholder for its local endpoint. No model weights or secret keys are in Git. See [Ollama's compatibility documentation](https://docs.ollama.com/api/openai-compatibility) and [GPU container documentation](https://docs.ollama.com/docker) for the supported endpoints and runtime arrangement.

The main test database was observed with **0 knowledge bases, 0 documents, 0 chunks** before changing its active profile. A confirmed-maintenance dry-run and atomic empty-table switch changed `vector(1536)`/`hash-v1` to `vector(1024)`/BGE-M3; direct PostgreSQL queries confirmed both dimensions and profile. There were no existing main-database vectors to rebuild. Separately, `scripts/check_local_reindex.py` created an isolated real pgvector database, indexed one public test document into four hash chunks, **actually rebuilt all four with BGE-M3** (311.47 ms in the second run), verified 1024 dimensions, rolled back to 1536 dimensions and four original chunks, then removed the temporary database. This is evidence for both paths, not a production migration claim.

The final fixed-config run is [local_real_final.json](evaluation/results/local_real_final.json), dated in its `run_utc` field. It uses exactly the same corpus SHA-256 and 20 positive / 4 unsupported cases as Phase 2, with Top-8, rerank-4 and 3200 context characters. The final run was invoked inside the AI container against real PostgreSQL/pgvector and the GPU model service. The full Go API upload/chat/citation path also passed its end-to-end regression against the live local-model stack.

| Final local-real metric | Observed |
| --- | ---: |
| Hit@8 / Recall@8 / MRR | 1.00 / 1.00 / 1.00 |
| Citation exact database-row validity | 21/21 |
| Citation in current knowledge base | 21/21 |
| Positive answer cites expected document | 20/20 |
| Positive non-refusal | 20/20 |
| Unsupported question abstains with no citation | 4/4 |

These are **source/document and structural metrics**, not a proof that every sentence is faithful. The corpus is small, synthetic and mostly direct fact lookup; the hash development provider also scored 1.00 on these retrieval metrics. No claim that the local model is better than the development implementation follows from this set.

### Observed latency and GPU baseline

The final run used one request at a time with models warm. Values below are per-question wall-clock measurements from the 24 cases, not concurrent throughput or production capacity. Median and P95 are over 24 calls; P95 uses the nearest-rank method. Five ingestion calls embedded 3, 4, 3, 4 and 3 chunks in batches.

| Stage | Median | P95 |
| --- | ---: | ---: |
| Query embedding | 11.80 ms | 19.64 ms |
| pgvector retrieval | 9.88 ms | 12.49 ms |
| Lexical rerank | 0.18 ms | 0.20 ms |
| LLM generation | 350.78 ms | 467.34 ms |
| End-to-end RAG function | 371.25 ms | 483.56 ms |

Actual document embedding batch calls took **203.64, 44.36, 36.75, 47.84 and 29.33 ms** in the final run. A separate first real two-text BGE-M3 call returned 1024-dimensional vectors in 187.1 ms. The first cold Qwen smoke call took 28,481 ms including model load; warm generation figures must not replace this cold-start observation. The final run reported 929 completion tokens over 8.66 seconds of summed generation-stage wall time, about **107.27 completion tokens per generation-stage wall second**; that denominator includes request and prompt work, so it is not the model's pure decode speed.

During the **earlier real-model run**, 42 two-second NVIDIA samples recorded total used GPU memory between **7,055 and 7,089 MiB**, with a sampled maximum GPU utilization of **93%**; [raw samples](evaluation/results/local_real_gpu_samples.csv) are retained. This is whole-GPU usage on the workstation, not Ollama-only VRAM. Idle-before-load `nvidia-smi` showed 1,834 MiB in use. The GPU sample run and final fixed-seed latency run are separate executions.

### Actual boundaries and prompt change

In the first case-level run with citation content, [local_real_verified.json](evaluation/results/local_real_verified.json), P01, P06 and P15 each returned an extra adjacent chunk that did **not** directly contain the answer fact. The cited rows were real and in scope, so automated citation validity still read 100%. This is why database provenance must not be mistaken for citation relevance.

The only response-quality change was a prompt sentence asking the model to cite the fewest chunks directly containing the answer fact. The corpus, chunking and retrieval parameters were unchanged. [The subsequent run](evaluation/results/local_real_prompt_after.json) removed those three extras but still over-cited P02. The final fixed-configuration run still over-cited P06 with an adjacent incident-reporting chunk. The earlier runs did not fix a random seed, so these observations do **not** isolate a causal improvement from the prompt; model variation remains a possible explanation. The failure evidence is retained rather than hidden.

[The separate answer review](evaluation/MANUAL_REVIEW_LOCAL.md) checks final-run P01–P10 against every returned chunk. The assistant judged **10/10 answers supported by at least one citation**, but only **9/10 cases had every returned chunk directly supporting the answer**. This is a subjective review by the assistant, not an independent human rating or automated semantic score. The remaining ten positive answers were not manually rated in that review. No additional parameter changes were made from this small set.
