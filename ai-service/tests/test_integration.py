"""Real PostgreSQL/pgvector integration. Set TEST_DATABASE_URL; never silently skip."""
import os
import uuid
from pathlib import Path

import psycopg

from app.providers import ExtractiveAnswer, HashEmbedding
from app.rag import chat, process_document


def test_pgvector_ingestion_scope_and_citation(tmp_path: Path, monkeypatch):
    database = os.environ["TEST_DATABASE_URL"]
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    first_kb, second_kb, document_id = (str(uuid.uuid4()) for _ in range(3))
    path = tmp_path / f"{document_id}.txt"
    path.write_text("\n\n".join(["The employee vacation policy grants twenty days of annual leave. " * 8,
                                "The security handbook requires two factor authentication. " * 8]), encoding="utf-8")
    with psycopg.connect(database) as conn:
        conn.execute("INSERT INTO knowledge_bases(id,name) VALUES(%s,%s),(%s,%s)", (first_kb, "integration A", second_kb, "integration B"))
        conn.execute("INSERT INTO documents(id,knowledge_base_id,original_filename,media_type,storage_path) VALUES(%s,%s,%s,%s,%s)",
                     (document_id, first_kb, "policy.txt", "text/plain", str(path)))
    try:
        count = process_document(document_id, HashEmbedding())
        assert count > 1
        with psycopg.connect(database) as conn:
            status, stored_count = conn.execute("SELECT status,chunk_count FROM documents WHERE id=%s", (document_id,)).fetchone()
            assert (status, stored_count) == ("ready", count)
            assert conn.execute("SELECT count(*) FROM document_chunks WHERE document_id=%s", (document_id,)).fetchone()[0] == count
        result = chat(first_kb, "How many days does the vacation policy grant?", HashEmbedding(), ExtractiveAnswer())
        assert result["retrieval"]["initial_top_k"]
        assert result["citations"]
        citation = result["citations"][0]
        assert citation["document_id"] == document_id
        with psycopg.connect(database) as conn:
            row = conn.execute("SELECT chunk_index,content FROM document_chunks WHERE id=%s", (citation["chunk_id"],)).fetchone()
        assert row == (citation["chunk_index"], citation["content"])
        isolated = chat(second_kb, "How many days does the vacation policy grant?", HashEmbedding(), ExtractiveAnswer())
        assert isolated["retrieval"]["initial_top_k"] == []
        assert isolated["citations"] == []
    finally:
        with psycopg.connect(database) as conn:
            conn.execute("DELETE FROM knowledge_bases WHERE id IN (%s,%s)", (first_kb, second_kb))
