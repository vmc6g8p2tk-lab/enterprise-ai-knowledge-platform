import os
import uuid

import psycopg
import pytest

from app.providers import HashEmbedding
from app.rag import chat, ensure_embedding_profile, process_document
from app.providers import ExtractiveAnswer


class ChangedHashModel(HashEmbedding):
    @property
    def model_id(self) -> str:
        return "hash-v2-incompatible"


def test_dimension_and_model_change_require_reindex(tmp_path, monkeypatch):
    database = os.environ["TEST_DATABASE_URL"]
    monkeypatch.setenv("DATABASE_URL", database)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    kb_id, doc_id = str(uuid.uuid4()), str(uuid.uuid4())
    path = tmp_path / f"{doc_id}.txt"
    path.write_text("The test policy grants 18 working days of leave.", encoding="utf-8")
    with psycopg.connect(database) as conn:
        conn.execute("INSERT INTO knowledge_bases(id,name) VALUES(%s,'Profile test')", (kb_id,))
        conn.execute(
            "INSERT INTO documents(id,knowledge_base_id,original_filename,media_type,storage_path) "
            "VALUES(%s,%s,'profile.txt','text/plain',%s)", (doc_id, kb_id, str(path)))
    try:
        process_document(doc_id, HashEmbedding())
        with psycopg.connect(database) as conn:
            with pytest.raises(ValueError, match="dimension"):
                ensure_embedding_profile(conn, HashEmbedding(64))
            conn.rollback()
            with pytest.raises(ValueError, match="reindex"):
                ensure_embedding_profile(conn, ChangedHashModel())
        with pytest.raises(ValueError, match="reindex"):
            chat(kb_id, "How many leave days?", ChangedHashModel(), ExtractiveAnswer())
    finally:
        with psycopg.connect(database) as conn:
            conn.execute("DELETE FROM knowledge_bases WHERE id=%s", (kb_id,))
