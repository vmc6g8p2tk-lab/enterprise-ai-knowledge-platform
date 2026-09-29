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
