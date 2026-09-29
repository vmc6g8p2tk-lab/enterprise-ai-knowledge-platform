import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql

from app.providers import HashEmbedding
from app.rag import process_document
from app.reindex import finish_reindex, staged_reindex


def test_staged_dimension_change_and_rollback_in_isolated_database(tmp_path, monkeypatch):
    import os
    admin_url = os.environ["TEST_DATABASE_URL"]
    db_name = "rag_reindex_" + uuid.uuid4().hex[:12]
    parts = urlsplit(admin_url)
    test_url = urlunsplit((parts.scheme, parts.netloc, "/" + db_name, parts.query, parts.fragment))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    try:
        migrations = Path(__file__).resolve().parents[2] / "backend" / "migrations"
        with psycopg.connect(test_url) as conn:
            for name in ("0001_initial.sql", "0002_embedding_profile.sql", "0003_reindex_backup.sql"):
                conn.execute((migrations / name).read_text(encoding="utf-8"))
        monkeypatch.setenv("DATABASE_URL", test_url)
        monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
        kb_id, doc_id = str(uuid.uuid4()), str(uuid.uuid4())
        file = tmp_path / f"{doc_id}.txt"
        file.write_text("The operations policy checks backups at 08:00 every morning.",
                        encoding="utf-8")
        with psycopg.connect(test_url) as conn:
            conn.execute("INSERT INTO knowledge_bases(id,name) VALUES(%s,'Reindex test')", (kb_id,))
            conn.execute(
                "INSERT INTO documents(id,knowledge_base_id,original_filename,media_type,storage_path) "
                "VALUES(%s,%s,'test.txt','text/plain',%s)", (doc_id, kb_id, str(file)))
        process_document(doc_id, HashEmbedding())
        dry = staged_reindex(test_url, tmp_path, HashEmbedding(64),
                             maintenance_confirmed=True, dry_run=True)
        assert dry["dry_run"] and dry["documents"] == 1
        result = staged_reindex(test_url, tmp_path, HashEmbedding(64),
                                maintenance_confirmed=True)
        assert result["staged_chunks"] == 1
        with psycopg.connect(test_url) as conn:
            assert conn.execute("SELECT vector_dims(embedding) FROM document_chunks").fetchone()[0] == 64
            assert conn.execute("SELECT dimensions FROM embedding_profile").fetchone()[0] == 64
        finish_reindex(test_url, "rollback", maintenance_confirmed=True)
        with psycopg.connect(test_url) as conn:
            assert conn.execute("SELECT vector_dims(embedding) FROM document_chunks").fetchone()[0] == 1536
            assert conn.execute("SELECT dimensions FROM embedding_profile").fetchone()[0] == 1536
            assert conn.execute("SELECT to_regclass('public.document_chunks_backup')").fetchone()[0] is None
        staged_reindex(test_url, tmp_path, HashEmbedding(64), maintenance_confirmed=True)
        finish_reindex(test_url, "finalize", maintenance_confirmed=True)
        with psycopg.connect(test_url) as conn:
            assert conn.execute("SELECT vector_dims(embedding) FROM document_chunks").fetchone()[0] == 64
            assert conn.execute("SELECT to_regclass('public.document_chunks_backup')").fetchone()[0] is None
            conn.execute("DELETE FROM knowledge_bases WHERE id=%s", (kb_id,))
        empty = staged_reindex(test_url, tmp_path, HashEmbedding(96),
                               maintenance_confirmed=True, dry_run=True)
        assert empty["empty_switch"] and empty["documents"] == 0
        switched = staged_reindex(test_url, tmp_path, HashEmbedding(96),
                                  maintenance_confirmed=True)
        assert switched["staged_chunks"] == 0
        with psycopg.connect(test_url) as conn:
            assert conn.execute("SELECT dimensions FROM embedding_profile").fetchone()[0] == 96
            assert conn.execute("SELECT format_type(a.atttypid,a.atttypmod) FROM pg_attribute a "
                                "JOIN pg_class t ON t.oid=a.attrelid "
                                "WHERE t.relname='document_chunks' AND a.attname='embedding'").fetchone()[0] == "vector(96)"
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))
