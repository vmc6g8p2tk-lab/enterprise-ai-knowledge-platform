"""Two-step real Compose restart check. State is stored only in an ignored temp file."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx
import psycopg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "verify"))
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    api = os.environ["E2E_API_URL"].rstrip("/")
    database = os.environ["E2E_DATABASE_URL"]
    password = "Public-fixture-restart-2026!"
    with httpx.Client(timeout=180) as client:
        if args.mode == "prepare":
            email = f"restart-{uuid.uuid4()}@example.test"
            registered = client.post(f"{api}/auth/register", json={"email": email, "password": password})
            assert registered.status_code == 201, registered.text
            session = client.post(f"{api}/auth/login", json={"email": email, "password": password})
            assert session.status_code == 200
            headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
            kb = client.post(f"{api}/knowledge-bases", headers=headers, json={"name": "Restart recovery test"})
            assert kb.status_code == 201
            kb_id = kb.json()["id"]
            document = client.post(f"{api}/knowledge-bases/{kb_id}/documents", headers=headers,
                                   files={"file": ("restart.txt", (
                                       "The restart recovery policy preserves durable jobs. " * 15 +
                                       "\n\nA second paragraph gives the worker multiple chunks. " * 15).encode())})
            assert document.status_code == 202
            doc_id = document.json()["id"]
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                row = next(item for item in client.get(f"{api}/knowledge-bases/{kb_id}/documents",
                                                     headers=headers).json() if item["id"] == doc_id)
                if row["status"] == "ready":
                    break
                time.sleep(0.5)
            assert row["status"] == "ready" and row["chunk_count"] > 1, row
            with psycopg.connect(database) as conn:
                conn.execute("UPDATE documents SET status='processing' WHERE id=%s", (doc_id,))
                conn.execute("UPDATE processing_jobs SET status='processing',attempt_count=1,"
                             "worker_id=%s,lease_until=now()-interval '1 second',finished_at=NULL WHERE document_id=%s",
                             (str(uuid.uuid4()), doc_id))
            args.state.write_text(json.dumps({"email": email, "user_id": registered.json()["id"],
                                             "kb_id": kb_id, "doc_id": doc_id,
                                             "expected_chunks": row["chunk_count"]}), encoding="utf-8")
            print("expired processing lease prepared; restart backend now")
        else:
            state = json.loads(args.state.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                with psycopg.connect(database) as conn:
                    job = conn.execute("SELECT status,attempt_count FROM processing_jobs WHERE document_id=%s",
                                       (state["doc_id"],)).fetchone()
                    document = conn.execute("SELECT status,chunk_count FROM documents WHERE id=%s",
                                            (state["doc_id"],)).fetchone()
                if job[0] == "completed" and document[0] == "ready":
                    break
                time.sleep(0.5)
            with psycopg.connect(database) as conn:
                chunks = conn.execute("SELECT chunk_index,count(*) FROM document_chunks WHERE document_id=%s "
                                      "GROUP BY chunk_index", (state["doc_id"],)).fetchall()
            result = {"job_status": job[0], "attempt_count": job[1], "document_status": document[0],
                      "chunk_count": document[1], "unique_chunk_indexes": len(chunks),
                      "duplicate_chunks": any(count != 1 for _, count in chunks)}
            assert result == {"job_status": "completed", "attempt_count": 2,
                              "document_status": "ready", "chunk_count": state["expected_chunks"],
                              "unique_chunk_indexes": state["expected_chunks"], "duplicate_chunks": False}, result
            session = client.post(f"{api}/auth/login", json={"email": state["email"], "password": password})
            assert session.status_code == 200
            headers = {"Authorization": f"Bearer {session.json()['access_token']}"}
            assert client.delete(f"{api}/knowledge-bases/{state['kb_id']}", headers=headers).status_code == 204
            with psycopg.connect(database) as conn:
                conn.execute("DELETE FROM users WHERE id=%s", (state["user_id"],))
            args.state.unlink()
            print(json.dumps(result))


if __name__ == "__main__":
    main()
