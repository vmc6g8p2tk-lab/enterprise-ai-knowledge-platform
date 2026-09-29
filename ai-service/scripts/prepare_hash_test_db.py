"""Prepare a separate vector(1536) database for development-provider regressions."""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql


def main() -> None:
    target_url = os.environ["TEST_DATABASE_URL"]
    parts = urlsplit(target_url)
    db_name = parts.path.lstrip("/")
    if db_name != "knowledge_test":
        raise ValueError("TEST_DATABASE_URL must name the dedicated knowledge_test database")
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres",
                           parts.query, parts.fragment))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (db_name,)).fetchone()
        if not exists:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    migrations = Path(__file__).resolve().parents[2] / "backend" / "migrations"
    with psycopg.connect(target_url) as conn:
        exists = conn.execute("SELECT to_regclass('public.knowledge_bases')").fetchone()[0]
        if exists is None:
            for name in ("0001_initial.sql", "0002_embedding_profile.sql",
                         "0003_reindex_backup.sql"):
                conn.execute((migrations / name).read_text(encoding="utf-8"))
        else:
            vector_type = conn.execute(
                "SELECT format_type(a.atttypid,a.atttypmod) FROM pg_attribute a "
                "JOIN pg_class t ON t.oid=a.attrelid "
                "WHERE t.relname='document_chunks' AND a.attname='embedding'").fetchone()
            if vector_type is None or vector_type[0] != "vector(1536)":
                raise ValueError("existing test database is not a vector(1536) test schema")
    print("knowledge_test is ready with vector(1536)")


if __name__ == "__main__":
    main()
