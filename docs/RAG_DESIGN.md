# RAG design — Phase 1

## Chunking

Text is cleaned while retaining paragraph breaks. Paragraphs are packing units, with sentence and word fallback for oversized paragraphs. The default target is at most 800 characters, with up to 120 characters of tail overlap at a word boundary. This keeps natural boundaries where possible and gives adjacent chunks some shared context. Character length is only a first approximation to tokenizer limits; multilingual/token-aware splitting should be evaluated later.

## Embeddings

`EmbeddingProvider.embed(text) -> list[float]` isolates the caller from the model. All providers must emit 1536 dimensions to match `vector(1536)`. The default `HashEmbedding` is a deterministic lexical projection for local development and test repeatability. It is **not a semantic embedding model**. `OpenAIEmbedding` is an optional real provider using a user key; changing providers on an existing collection requires re-embedding every chunk, because mixed vector spaces cannot be compared meaningfully. Real provider behavior has not been validated without credentials.

## Retrieval and Top-K

The query embedding is passed as a parameter to PostgreSQL and cast to `vector`. `<=>` computes cosine distance. The join from `document_chunks` through `documents` applies `knowledge_base_id` and `status='ready'` before the `LIMIT 8`. The Phase 1 dataset uses exact distance ordering; no approximate vector index is installed because small datasets do not justify ANN tuning or recall tradeoffs. A B-tree on document knowledge base ID and a chunk document ID index support the join. Trace output records the initial chunk IDs and distances.

## Rerank

`Reranker.rerank(question, candidates, limit)` is a replaceable interface. The first implementation combines question/content lexical overlap (70%) and vector similarity (30%), then keeps 4 chunks. This is a lightweight baseline, not a learned cross-encoder. The response records selected IDs, distances and rerank scores for later evaluation.

## Context, answer and citations

The development answer provider selects a relevant sentence from selected chunks. It returns the selected chunk's **index**, and the service builds citation fields from database rows. The optional OpenAI-compatible answer provider requests JSON with text and context indices. Invalid indices are dropped. The prompt tells the model to use only supplied context and admit missing evidence. This reduces basic hallucination but does not guarantee factuality or defend comprehensively against prompt injection. An unsupported answer returns no citations. Citation provenance means the IDs/content are genuine retrieved rows; it does not by itself prove that every sentence in a real model's answer is supported.

## Remaining evaluation work

Add a labeled question set, retrieval recall, answer faithfulness checks, and real-model integration tests before claiming semantic quality. Measure parser edge cases, latency, token budget and costs under controlled conditions. No benchmark or model-quality number is claimed in this phase.
