import json
import os
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .chunking import chunk_text
from .parsing import extract_text
from .providers import AnswerProvider, EmbeddingProvider, terms


TOP_K = 8
RERANK_K = 4


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise ValueError("DATABASE_URL is required")
    return value


def vector_literal(values: list[float]) -> str:
    if len(values) != 1536:
        raise ValueError("embedding must have 1536 dimensions")
    return "[" + ",".join(f"{value:.9g}" for value in values) + "]"


def process_document(document_id: str, embedder: EmbeddingProvider) -> int:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        row = conn.execute("SELECT id,storage_path FROM documents WHERE id=%s", (document_id,)).fetchone()
        if row is None:
            raise LookupError("document not found")
        conn.execute("UPDATE documents SET status='processing', failure_reason='',updated_at=now() WHERE id=%s", (document_id,))
        conn.commit()
        try:
            upload_root = Path(os.getenv("UPLOAD_DIR", "/data/uploads")).resolve()
            path = Path(row["storage_path"]).resolve()
            if not path.is_relative_to(upload_root):
                raise ValueError("document path escapes upload directory")
            chunks = chunk_text(extract_text(path))
            if not chunks:
                raise ValueError("document has no chunks")
            embeddings = [vector_literal(embedder.embed(chunk)) for chunk in chunks]
            with conn.transaction():
                conn.execute("DELETE FROM document_chunks WHERE document_id=%s", (document_id,))
                for index, (chunk, vector) in enumerate(zip(chunks, embeddings)):
                    conn.execute("INSERT INTO document_chunks(document_id,chunk_index,content,embedding,metadata) VALUES(%s,%s,%s,%s::vector,%s::jsonb)",
                        (document_id, index, chunk, vector, json.dumps({"strategy": "paragraph-boundary-v1"})))
                conn.execute("UPDATE documents SET status='ready',chunk_count=%s,failure_reason='',updated_at=now() WHERE id=%s", (len(chunks), document_id))
            return len(chunks)
        except Exception as exc:
            conn.rollback()
            conn.execute("UPDATE documents SET status='failed',chunk_count=0,failure_reason=%s,updated_at=now() WHERE id=%s", (str(exc)[:500], document_id))
            conn.commit()
            raise


def rerank(question: str, candidates: list[dict]) -> list[dict]:
    query_terms = terms(question)
    for candidate in candidates:
        content_terms = terms(candidate["content"])
        lexical = len(query_terms & content_terms) / max(1, len(query_terms))
        candidate["rerank_score"] = round(0.7 * lexical + 0.3 * max(0.0, 1.0 - candidate["distance"]), 6)
    return sorted(candidates, key=lambda item: item["rerank_score"], reverse=True)[:RERANK_K]


def chat(knowledge_base_id: str, question: str, embedder: EmbeddingProvider, answerer: AnswerProvider) -> dict:
    vector = vector_literal(embedder.embed(question))
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT c.id AS chunk_id,c.document_id,d.original_filename AS document_name,
                   c.chunk_index,c.content,(c.embedding <=> %s::vector) AS distance
            FROM document_chunks c JOIN documents d ON d.id=c.document_id
            WHERE d.knowledge_base_id=%s AND d.status='ready'
            ORDER BY c.embedding <=> %s::vector LIMIT %s
        """, (vector, knowledge_base_id, vector, TOP_K)).fetchall()
    candidates = [dict(row) for row in rows]
    initial = [{"chunk_id": row["chunk_id"], "distance": row["distance"]} for row in candidates]
    selected = rerank(question, candidates)
    answer, used_indices = answerer.answer(question, selected)
    citations = [
        {"document_id": selected[index]["document_id"], "document_name": selected[index]["document_name"],
         "chunk_id": selected[index]["chunk_id"], "chunk_index": selected[index]["chunk_index"],
         "content": selected[index]["content"]}
        for index in used_indices
    ]
    return {"answer": answer, "citations": citations,
            "retrieval": {"initial_top_k": initial,
                          "reranked": [{"chunk_id": row["chunk_id"], "distance": row["distance"], "rerank_score": row["rerank_score"]} for row in selected]},
            "answer_mode": "extractive-dev" if answerer.__class__.__name__ == "ExtractiveAnswer" else "openai-unverified"}
