# RAG design — Phase 1

## Chunking

Text is cleaned while retaining paragraph breaks. Paragraphs are packing units, with sentence and word fallback for oversized paragraphs. The default target is at most 800 characters, with up to 120 characters of tail overlap at a word boundary. This keeps natural boundaries where possible and gives adjacent chunks some shared context. Character length is only a first approximation to tokenizer limits; multilingual/token-aware splitting should be evaluated later.

## Embeddings

The EmbeddingProvider interface exposes provider name, model identity, dimensions, single-text embed and batch embed. Ingestion passes all chunks to embed_batch; the OpenAI-compatible adapter sends bounded groups (default 32) to the [Embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create), restores response order by index and validates every returned vector. The default HashEmbedding is a deterministic lexical projection for local development, **not a semantic model**. A singleton DB profile and pgvector column check reject mixed models and dimension mismatches. An existing corpus needs the [staged reindex workflow](REINDEX.md) before switching even between same-dimensional models.

OpenAI-compatible calls have per-attempt timeout, at most two retries by default, retry only transient HTTP/network failures, honor a bounded Retry-After, and sanitize errors so credentials and response bodies do not enter ordinary logs. The [provider rate-limit guidance](https://developers.openai.com/api/docs/guides/rate-limits) informed those bounds. Real provider behavior remains unverified without a key.

## Retrieval and Top-K

The query embedding is passed as a parameter to PostgreSQL and cast to `vector`. `<=>` computes cosine distance. The join from `document_chunks` through `documents` applies `knowledge_base_id` and `status='ready'` before the `LIMIT 8`. The Phase 1 dataset uses exact distance ordering; no approximate vector index is installed because small datasets do not justify ANN tuning or recall tradeoffs. A B-tree on document knowledge base ID and a chunk document ID index support the join. Trace output records the initial chunk IDs and distances.

## Rerank

`Reranker.rerank(question, candidates, limit)` is a replaceable interface. The first implementation combines question/content lexical overlap (70%) and vector similarity (30%), then keeps 4 chunks. This is a lightweight baseline, not a learned cross-encoder. The response records selected IDs, distances and rerank scores for later evaluation.

## Context, answer and citations

The AnswerProvider.generate interface returns text, context indices, optional token usage and a structured abstained flag. The development answer provider selects a relevant sentence only with at least two shared non-stopword terms and abstains on explicit absence language; this is a lexical test baseline. The optional OpenAI-compatible answer provider requests JSON from [Chat Completions](https://developers.openai.com/api/reference/resources/chat), with text, context indices and an insufficient-evidence flag. The service validates indices and constructs citation fields from actual retrieved database rows. A no-context request abstains before any LLM call. The prompt tells the model to use only supplied context and admit missing evidence. This does not guarantee factuality or comprehensive prompt-injection defense. Citation provenance does not prove each claim is supported.

Development debug responses record query, candidate chunk contents and cosine similarity, rerank order, final context, character count, estimated tokens, provider usage tokens when returned, and per-stage/total latency. Ordinary server logs keep only knowledge-base IDs, counts, lengths and time; full uploaded text and secret headers are never intentionally logged.

## Remaining evaluation work

A versioned 20-positive/4-unsupported labeled set now reports Hit@K, Recall@K, MRR, citation validity/scope/expected document and unsupported refusal. The [evaluation report](RAG_EVALUATION.md) shows a development-mode before/after. The small, synthetic set does not establish semantic answer quality. Real-model integration, human-reviewed faithfulness, broader parser edge cases, token budget and costs remain unverified. No production performance number is claimed.
