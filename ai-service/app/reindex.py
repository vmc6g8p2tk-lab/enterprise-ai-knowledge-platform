"""Maintenance-only staged vector rebuild with atomic cutover and rollback."""

import argparse
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .chunking import chunk_text
from .parsing import extract_text
from .providers import EmbeddingProvider, providers
from .rag import vector_literal


LOCK_ID = 7176512


def _relation_exists(conn: psycopg.Connection, name: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) AS relation", (f"public.{name}",)).fetchone()
    return row["relation"] is not None


def _safe_source(path_text: str, upload_root: Path) -> Path:
    path = Path(path_text).resolve()
    if not path.is_relative_to(upload_root) or not path.is_file():
        raise ValueError("a stored original file is missing or outside the upload volume")
    return path


def staged_reindex(db_url: str, upload_dir: Path, embedder: EmbeddingProvider,
                   *, maintenance_confirmed: bool, dry_run: bool = False) -> dict:
    if not maintenance_confirmed:
        raise ValueError("stop API/AI services and confirm maintenance before reindex")
    if not 1 <= embedder.dimensions <= 2000:
        raise ValueError("pgvector vector dimensions must be within 1..2000")
    upload_root = upload_dir.resolve()
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            if _relation_exists(conn, "document_chunks_stage") or _relation_exists(conn, "document_chunks_backup"):
                raise ValueError("a previous reindex stage or backup exists; inspect, rollback or finalize first")
            current = conn.execute(
                "SELECT provider_name,model_id,dimensions FROM embedding_profile WHERE singleton=true"
            ).fetchone()
            if current is None:
                raise ValueError("active embedding profile is missing")
            documents = conn.execute(
                "SELECT id,storage_path FROM documents WHERE status='ready' ORDER BY id").fetchall()
            if not documents:
                raise ValueError("there are no ready documents to reindex")
            sources = [(row["id"], _safe_source(row["storage_path"], upload_root))
                       for row in documents]
            summary = {
                "documents": len(sources), "from": dict(current),
                "to": {"provider_name": embedder.provider_name, "model_id": embedder.model_id,
                       "dimensions": embedder.dimensions},
            }
            if dry_run:
                return {**summary, "dry_run": True}
            conn.commit()  # Do not hold a database transaction during model calls.
            conn.execute(f"""
                CREATE TABLE document_chunks_stage (
                    id bigserial PRIMARY KEY,
                    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
                    content text NOT NULL CHECK (length(content) > 0),
                    embedding vector({embedder.dimensions}) NOT NULL,
                    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE(document_id,chunk_index)
                )
            """)
            conn.execute(
                "CREATE INDEX document_chunks_stage_document_id_idx "
                "ON document_chunks_stage(document_id)")
            conn.commit()
            count = 0
            for document_id, path in sources:
                chunks = chunk_text(extract_text(path))
                if not chunks:
                    raise ValueError("a ready document produced no chunks")
                vectors = embedder.embed_batch(chunks)
                if len(vectors) != len(chunks):
                    raise ValueError("embedding provider returned the wrong count")
                with conn.transaction():
                    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                        conn.execute(
                            "INSERT INTO document_chunks_stage(document_id,chunk_index,content,embedding,metadata) "
                            "VALUES(%s,%s,%s,%s::vector,%s::jsonb)",
                            (document_id, index, chunk, vector_literal(vector, embedder.dimensions),
                             json.dumps({"strategy": "paragraph-boundary-v1"})))
                count += len(chunks)
            with conn.transaction():
                conn.execute("LOCK TABLE document_chunks IN ACCESS EXCLUSIVE MODE")
                actual = conn.execute("SELECT count(*) AS total FROM document_chunks_stage").fetchone()["total"]
                if actual != count:
                    raise ValueError("staged chunk count mismatch")
                conn.execute(
                    "INSERT INTO embedding_profile_backup(singleton,provider_name,model_id,dimensions) "
                    "SELECT singleton,provider_name,model_id,dimensions FROM embedding_profile")
                conn.execute("ALTER TABLE document_chunks RENAME TO document_chunks_backup")
                conn.execute("ALTER TABLE document_chunks_stage RENAME TO document_chunks")
                conn.execute(
                    "UPDATE embedding_profile SET provider_name=%s,model_id=%s,dimensions=%s,updated_at=now() "
                    "WHERE singleton=true",
                    (embedder.provider_name, embedder.model_id, embedder.dimensions))
                conn.execute(
                    "UPDATE documents SET chunk_count=("
                    "SELECT count(*) FROM document_chunks c WHERE c.document_id=documents.id"
                    ") WHERE status='ready'")
            return {**summary, "dry_run": False, "staged_chunks": count,
                    "backup_table": "document_chunks_backup"}
        except Exception:
            conn.rollback()
            if _relation_exists(conn, "document_chunks_stage"):
                conn.execute("DROP TABLE document_chunks_stage")
                conn.commit()
            raise
        finally:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            conn.commit()


def finish_reindex(db_url: str, mode: str, *, maintenance_confirmed: bool) -> None:
    if not maintenance_confirmed or mode not in {"rollback", "finalize"}:
        raise ValueError("rollback/finalize requires confirmed maintenance")
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            if not _relation_exists(conn, "document_chunks_backup"):
                raise ValueError("no reindex backup exists")
            previous = conn.execute(
                "SELECT provider_name,model_id,dimensions FROM embedding_profile_backup "
                "WHERE singleton=true").fetchone()
            if previous is None:
                raise ValueError("reindex backup profile is missing")
            conn.commit()
            with conn.transaction():
                conn.execute("LOCK TABLE document_chunks IN ACCESS EXCLUSIVE MODE")
                if mode == "rollback":
                    conn.execute("ALTER TABLE document_chunks RENAME TO document_chunks_failed")
                    conn.execute("ALTER TABLE document_chunks_backup RENAME TO document_chunks")
                    conn.execute(
                        "UPDATE embedding_profile SET provider_name=%s,model_id=%s,dimensions=%s,"
                        "updated_at=now() WHERE singleton=true",
                        (previous["provider_name"], previous["model_id"], previous["dimensions"]))
                    conn.execute("DROP TABLE document_chunks_failed")
                    conn.execute(
                        "UPDATE documents SET chunk_count=("
                        "SELECT count(*) FROM document_chunks c WHERE c.document_id=documents.id"
                        ") WHERE status='ready'")
                else:
                    conn.execute("DROP TABLE document_chunks_backup")
                conn.execute("DELETE FROM embedding_profile_backup WHERE singleton=true")
        finally:
            conn.rollback()
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Staged maintenance-only vector reindex")
    parser.add_argument("--mode", choices=["dry-run", "build", "rollback", "finalize"],
                        default="dry-run")
    parser.add_argument("--confirm-maintenance", action="store_true",
                        help="Confirm backend and AI services are stopped")
    args = parser.parse_args()
    db_url = os.environ["DATABASE_URL"]
    if args.mode in {"rollback", "finalize"}:
        finish_reindex(db_url, args.mode, maintenance_confirmed=args.confirm_maintenance)
        print(json.dumps({"mode": args.mode, "completed": True}))
        return
    embedder, _ = providers()
    result = staged_reindex(db_url, Path(os.environ.get("UPLOAD_DIR", "/data/uploads")),
                            embedder, maintenance_confirmed=args.confirm_maintenance,
                            dry_run=args.mode == "dry-run")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
