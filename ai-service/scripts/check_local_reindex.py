"""Destructive only to its own temporary DB: exercise hash -> real BGE-M3 -> rollback."""

import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from app.providers import HashEmbedding, OpenAIEmbedding
from app.rag import process_document
from app.reindex import finish_reindex, staged_reindex


def main() -> None:
    admin_url = os.environ["TEST_DATABASE_URL"]
    base_url = os.environ.get("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
    if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("this acceptance script only targets a loopback local model")
    corpus = (Path(__file__).resolve().parents[2] / "docs" / "evaluation" / "corpus").resolve()
    original = corpus / "people-handbook.md"
    db_name = "rag_local_reindex_" + uuid.uuid4().hex[:12]
    parts = urlsplit(admin_url)
    test_url = urlunsplit((parts.scheme, parts.netloc, "/" + db_name,
                          parts.query, parts.fragment))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    old_db, old_upload = os.environ.get("DATABASE_URL"), os.environ.get("UPLOAD_DIR")
    try:
        migrations = Path(__file__).resolve().parents[2] / "backend" / "migrations"
        with psycopg.connect(test_url) as conn:
            for name in ("0001_initial.sql", "0002_embedding_profile.sql",
                         "0003_reindex_backup.sql"):
                conn.execute((migrations / name).read_text(encoding="utf-8"))
        os.environ["DATABASE_URL"] = test_url
        os.environ["UPLOAD_DIR"] = str(corpus)
        kb_id, document_id = str(uuid.uuid4()), str(uuid.uuid4())
        with psycopg.connect(test_url) as conn:
            conn.execute("INSERT INTO knowledge_bases(id,name) VALUES(%s,%s)",
                         (kb_id, "Temporary local model reindex acceptance"))
            conn.execute(
                "INSERT INTO documents(id,knowledge_base_id,original_filename,media_type,storage_path) "
                "VALUES(%s,%s,%s,'text/markdown',%s)",
                (document_id, kb_id, original.name, str(original)))
        before_chunks = process_document(document_id, HashEmbedding())
        real = OpenAIEmbedding("ollama", base_url, "bge-m3:567m-fp16",
                               dimensions=1024, batch_size=16)
        dry = staged_reindex(test_url, corpus, real,
                             maintenance_confirmed=True, dry_run=True)
        if dry["documents"] != 1 or dry["from"]["dimensions"] != 1536:
            raise AssertionError("unexpected pre-reindex state")
        started = time.perf_counter()
        built = staged_reindex(test_url, corpus, real, maintenance_confirmed=True)
        reindex_ms = round((time.perf_counter() - started) * 1000, 2)
        with psycopg.connect(test_url) as conn:
            dim, count = conn.execute(
                "SELECT min(vector_dims(embedding)),count(*) FROM document_chunks").fetchone()
            profile = conn.execute(
                "SELECT provider_name,model_id,dimensions FROM embedding_profile").fetchone()
        if dim != 1024 or count != before_chunks or profile[2] != 1024:
            raise AssertionError("real-vector cutover validation failed")
        finish_reindex(test_url, "rollback", maintenance_confirmed=True)
        with psycopg.connect(test_url) as conn:
            rollback_dim, rollback_count = conn.execute(
                "SELECT min(vector_dims(embedding)),count(*) FROM document_chunks").fetchone()
            rollback_profile = conn.execute(
                "SELECT provider_name,model_id,dimensions FROM embedding_profile").fetchone()
        if rollback_dim != 1536 or rollback_count != before_chunks or rollback_profile[0] != "hash":
            raise AssertionError("rollback validation failed")
        result = {"source": original.name, "documents": 1,
                  "chunks": before_chunks, "real_model": profile[1],
                  "real_dimensions": dim, "reindex_ms": reindex_ms,
                  "rollback_dimensions": rollback_dim,
                  "rollback_chunks": rollback_count}
    finally:
        if old_db is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_db
        if old_upload is None:
            os.environ.pop("UPLOAD_DIR", None)
        else:
            os.environ["UPLOAD_DIR"] = old_upload
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))
    print(json.dumps({**result, "temporary_database_removed": True}))


if __name__ == "__main__":
    main()
