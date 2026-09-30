"""Document ingestion and knowledge-base scoped retrieval."""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.rows import dict_row, tuple_row

from .chunking import chunk_text
from .metrics import METRICS
from .parsing import extract_text
from .providers import AnswerProvider, EmbeddingProvider, GenerationResult, INSUFFICIENT
from .reranking import LexicalReranker, Reranker


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievalConfig:
    top_k: int = 8
    rerank_k: int = 4
    max_context_chars: int = 3200

    def __post_init__(self):
        if not 1 <= self.rerank_k <= self.top_k <= 100 or self.max_context_chars < 100:
            raise ValueError("invalid retrieval configuration")


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise ValueError("DATABASE_URL is required")
    return value


def vector_literal(values: list[float], dimensions: int) -> str:
    if len(values) != dimensions:
        raise ValueError(f"embedding has {len(values)} dimensions; expected {dimensions}")
    return "[" + ",".join(f"{value:.9g}" for value in values) + "]"


def ensure_embedding_profile(conn: psycopg.Connection, embedder: EmbeddingProvider) -> None:
    """Refuse mixed model spaces and any vector/schema dimension mismatch."""
    # Serialize first-time binding or safe switch when the vector table is empty.
    with conn.cursor(row_factory=tuple_row) as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(7176512)")
        cursor.execute("""
            SELECT format_type(a.atttypid,a.atttypmod)
            FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid
            WHERE t.relname='document_chunks' AND a.attname='embedding' AND NOT a.attisdropped
        """)
        row = cursor.fetchone()
        match = re.fullmatch(r"vector\((\d+)\)", row[0]) if row else None
        if not match:
            raise ValueError("document_chunks.embedding must have a fixed pgvector dimension")
        schema_dimensions = int(match.group(1))
        if schema_dimensions != embedder.dimensions:
            raise ValueError(
                f"embedding dimension {embedder.dimensions} does not match pgvector({schema_dimensions}); "
                "run the documented staged reindex procedure")
        expected = (embedder.provider_name, embedder.model_id, embedder.dimensions)
        cursor.execute(
            "SELECT provider_name,model_id,dimensions FROM embedding_profile WHERE singleton=true")
        current = cursor.fetchone()
        if current is None:
            cursor.execute("SELECT count(*) FROM document_chunks")
            if cursor.fetchone()[0]:
                raise ValueError("legacy vectors have no embedding profile; explicit adoption is required")
            cursor.execute(
                "INSERT INTO embedding_profile(singleton,provider_name,model_id,dimensions) "
                "VALUES(true,%s,%s,%s)", expected)
        elif current != expected:
            cursor.execute("SELECT count(*) FROM document_chunks")
            if cursor.fetchone()[0]:
                raise ValueError("embedding model differs from stored vectors; reindex is required")
            cursor.execute(
                "UPDATE embedding_profile SET provider_name=%s,model_id=%s,dimensions=%s,updated_at=now() "
                "WHERE singleton=true", expected)


def process_document(document_id: str, embedder: EmbeddingProvider) -> int:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        # Session advisory lock serializes even a reclaimed job with an old worker.
        # Connection close releases it after a crash; chunk replacement is transactional.
        conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (document_id,))
        conn.commit()
        row = conn.execute("SELECT id,storage_path FROM documents WHERE id=%s", (document_id,)).fetchone()
        if row is None:
            raise LookupError("document not found")
        try:
            ensure_embedding_profile(conn, embedder)
            conn.execute(
                "UPDATE documents SET status='processing',failure_reason='',updated_at=now() WHERE id=%s",
                (document_id,))
            conn.commit()
            upload_root = Path(os.getenv("UPLOAD_DIR", "/data/uploads")).resolve()
            path = Path(row["storage_path"]).resolve()
            if not path.is_relative_to(upload_root):
                raise ValueError("document path escapes upload directory")
            chunks = chunk_text(extract_text(path))
            if not chunks:
                raise ValueError("document has no chunks")
            # The provider batches internally. Never hold a DB transaction across remote calls.
            started_embedding = time.perf_counter()
            embeddings = embedder.embed_batch(chunks)
            if len(embeddings) != len(chunks):
                raise ValueError("embedding provider returned the wrong count")
            METRICS.observe("embedding_duration_seconds", (time.perf_counter() - started_embedding) * 1000)
            literals = [vector_literal(vector, embedder.dimensions) for vector in embeddings]
            with conn.transaction():
                conn.execute("DELETE FROM document_chunks WHERE document_id=%s", (document_id,))
                for index, (chunk, vector) in enumerate(zip(chunks, literals)):
                    conn.execute(
                        "INSERT INTO document_chunks(document_id,chunk_index,content,embedding,metadata) "
                        "VALUES(%s,%s,%s,%s::vector,%s::jsonb)",
                        (document_id, index, chunk, vector,
                         json.dumps({"strategy": "paragraph-boundary-v1"})))
                conn.execute(
                    "UPDATE documents SET status='ready',chunk_count=%s,failure_reason='',updated_at=now() "
                    "WHERE id=%s", (len(chunks), document_id))
            return len(chunks)
        except Exception as exc:
            conn.rollback()
            conn.execute(
                "UPDATE documents SET status='failed',chunk_count=0,failure_reason=%s,updated_at=now() "
                "WHERE id=%s", (str(exc)[:500], document_id))
            conn.commit()
            raise


def _trace_row(row: dict) -> dict:
    return {
        "chunk_id": row["chunk_id"],
        "document_id": str(row["document_id"]),
        "document_name": row["document_name"],
        "chunk_index": row["chunk_index"],
        "distance": row["distance"],
        "similarity": 1.0 - row["distance"],
    }


def chat(knowledge_base_id: str, question: str, embedder: EmbeddingProvider,
         answerer: AnswerProvider, reranker: Reranker | None = None,
         config: RetrievalConfig | None = None, debug: bool | None = None) -> dict:
    config = config or RetrievalConfig()
    debug = (os.getenv("RAG_DEBUG", "0") == "1") if debug is None else debug
    started = time.perf_counter()
    query_vector = vector_literal(embedder.embed(question), embedder.dimensions)
    embedding_ms = (time.perf_counter() - started) * 1000
    db_start = time.perf_counter()
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        ensure_embedding_profile(conn, embedder)
        rows = conn.execute("""
            SELECT c.id AS chunk_id,c.document_id,d.original_filename AS document_name,
                   c.chunk_index,c.content,(c.embedding <=> %s::vector) AS distance
            FROM document_chunks c JOIN documents d ON d.id=c.document_id
            WHERE d.knowledge_base_id=%s AND d.status='ready'
            ORDER BY c.embedding <=> %s::vector LIMIT %s
        """, (query_vector, knowledge_base_id, query_vector, config.top_k)).fetchall()
    db_ms = (time.perf_counter() - db_start) * 1000
    candidates = [dict(row) for row in rows]
    initial = [_trace_row(row) for row in candidates]
    rank_start = time.perf_counter()
    ranked = (reranker or LexicalReranker()).rerank(question, candidates, config.rerank_k)
    selected: list[dict] = []
    context_chars = 0
    for row in ranked:
        new_length = context_chars + len(row["content"]) + (2 if selected else 0)
        if new_length > config.max_context_chars:
            continue
        selected.append(row)
        context_chars = new_length
    rank_ms = (time.perf_counter() - rank_start) * 1000
    answer_start = time.perf_counter()
    generation = (answerer.generate(question, selected) if selected
                  else GenerationResult(INSUFFICIENT, [], abstained=True))
    generation_ms = (time.perf_counter() - answer_start) * 1000
    indices = sorted({index for index in generation.citation_indices
                      if type(index) is int and 0 <= index < len(selected)})
    citations = [
        {"document_id": str(selected[index]["document_id"]),
         "document_name": selected[index]["document_name"],
         "chunk_id": selected[index]["chunk_id"],
         "chunk_index": selected[index]["chunk_index"],
         "content": selected[index]["content"]}
        for index in indices
    ]
    total_ms = (time.perf_counter() - started) * 1000
    METRICS.observe("rag_request_duration_seconds", total_ms)
    METRICS.observe("embedding_duration_seconds", embedding_ms)
    METRICS.observe("retrieval_duration_seconds", db_ms)
    METRICS.observe("llm_duration_seconds", generation_ms)
    reranked = [{**_trace_row(row), "rerank_score": row["rerank_score"]} for row in ranked]
    result = {
        "answer": generation.answer,
        "abstained": generation.abstained,
        "citations": citations,
        "retrieval": {"initial_top_k": initial, "reranked": reranked},
        "answer_mode": answerer.provider_name,
    }
    if debug:
        result["debug"] = {
            "query": question,
            "retrieved_chunks": [{**_trace_row(row), "content": row["content"]}
                                 for row in candidates],
            "rerank_before": [row["chunk_id"] for row in candidates],
            "rerank_after": [row["chunk_id"] for row in ranked],
            "context_chunks": [{"chunk_id": row["chunk_id"], "content": row["content"]}
                               for row in selected],
            "query_chars": len(question),
            "context_chars": context_chars,
            "context_tokens_estimate": round(context_chars / 4),
            "provider_usage_tokens": generation.usage,
            "latency_ms": {"embedding": round(embedding_ms, 2), "retrieval_db": round(db_ms, 2),
                           "rerank": round(rank_ms, 2), "generation": round(generation_ms, 2),
                           "total": round(total_ms, 2)},
        }
    # Ordinary logs contain IDs and lengths only; no query, document text or auth headers.
    logger.info("rag kb=%s query_chars=%d retrieved=%d context=%d total_ms=%.1f",
                knowledge_base_id, len(question), len(candidates), len(selected), total_ms)
    return result
