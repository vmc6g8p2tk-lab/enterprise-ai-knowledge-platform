"""Full Compose auth, RBAC, asynchronous ingestion and citation isolation."""

import os
import time
import uuid

import httpx
import psycopg


def test_auth_rbac_async_and_scoped_rag():
    api = os.environ["E2E_API_URL"].rstrip("/")
    database = os.environ.get("E2E_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    created_users = []
    with httpx.Client(timeout=300) as client:
        def user(label):
            email, password = f"phase3-{label}-{uuid.uuid4()}@example.test", "Phase3-test-password!"
            created = client.post(f"{api}/auth/register", json={"email": email, "password": password})
            assert created.status_code == 201, created.text
            created_users.append(created.json()["id"])
            login = client.post(f"{api}/auth/login", json={"email": email, "password": password})
            assert login.status_code == 200, login.text
            return created.json()["id"], {"Authorization": f"Bearer {login.json()['access_token']}"}

        admin_id, admin = user("admin")
        editor_id, editor = user("editor")
        viewer_id, viewer = user("viewer")
        _, outsider = user("outsider")
        assert client.get(f"{api}/knowledge-bases").status_code == 401
        first = client.post(f"{api}/knowledge-bases", headers=admin, json={"name": "Phase3 RBAC A"})
        second = client.post(f"{api}/knowledge-bases", headers=admin, json={"name": "Phase3 RBAC B"})
        assert first.status_code == second.status_code == 201
        kb_a, kb_b = first.json()["id"], second.json()["id"]
        try:
            for uid, role in ((editor_id, "editor"), (viewer_id, "viewer")):
                granted = client.put(f"{api}/knowledge-bases/{kb_a}/members/{uid}",
                                     headers=admin, json={"role": role})
                assert granted.status_code == 200, granted.text
            assert client.post(f"{api}/knowledge-bases/{kb_a}/documents", headers=viewer,
                               files={"file": ("reject.txt", b"viewer cannot upload")}).status_code == 403
            assert client.delete(f"{api}/knowledge-bases/{kb_a}", headers=editor).status_code == 403
            assert client.get(f"{api}/knowledge-bases/{kb_a}", headers=outsider).status_code == 404
            assert client.get(f"{api}/knowledge-bases/{kb_b}", headers=viewer).status_code == 404
            assert client.post(f"{api}/knowledge-bases/{kb_b}/chat", headers=viewer,
                               json={"question": "What is the leave policy?"}).status_code == 404
            assert client.post(f"{api}/knowledge-bases/{kb_b}/agent", headers=viewer,
                               json={"question": "What is the leave policy?"}).status_code == 404
            assert all(item["id"] == kb_a for item in client.get(
                f"{api}/knowledge-bases", headers=viewer).json())

            content = ("Phase three travel policy reimburses train tickets up to 1200 CNY. " * 12 +
                       "\n\nThe incident response contact is the internal security desk. " * 12)
            start = time.perf_counter()
            upload = client.post(f"{api}/knowledge-bases/{kb_a}/documents", headers=editor,
                                 files={"file": ("phase3-policy.txt", content.encode())})
            upload_ms = (time.perf_counter() - start) * 1000
            assert upload.status_code == 202, upload.text
            document_id = upload.json()["id"]
            assert upload.json()["status"] in {"pending", "processing", "ready"}
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                row = next(item for item in client.get(f"{api}/knowledge-bases/{kb_a}/documents",
                                                       headers=viewer).json() if item["id"] == document_id)
                if row["status"] in {"ready", "failed"}:
                    break
                time.sleep(0.5)
            assert row["status"] == "ready", row
            assert row["chunk_count"] > 1
            with psycopg.connect(database) as conn:
                job = conn.execute("SELECT status,attempt_count,started_at,finished_at,created_at "
                                   "FROM processing_jobs WHERE document_id=%s", (document_id,)).fetchone()
                assert job[0] == "completed" and job[1] == 1
                assert conn.execute("SELECT count(*) FROM document_chunks WHERE document_id=%s",
                                    (document_id,)).fetchone()[0] == row["chunk_count"]
            question = "What is the train ticket reimbursement limit in the phase three travel policy?"
            response = client.post(f"{api}/knowledge-bases/{kb_a}/chat", headers=viewer,
                                   json={"question": question})
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["citations"]
            with psycopg.connect(database) as conn:
                for citation in result["citations"]:
                    row = conn.execute("SELECT d.knowledge_base_id,c.content FROM document_chunks c "
                                       "JOIN documents d ON d.id=c.document_id WHERE c.id=%s",
                                       (citation["chunk_id"],)).fetchone()
                    assert str(row[0]) == kb_a and row[1] == citation["content"]
            agent = client.post(f"{api}/knowledge-bases/{kb_a}/agent", headers=viewer,
                                json={"question": question})
            assert agent.status_code == 200, agent.text
            tool_result = agent.json()
            assert tool_result["tool_calls"] and tool_result["tool_calls"][0]["tool_name"] == "search_knowledge_base"
            assert tool_result["citations"]
            with psycopg.connect(database) as conn:
                for citation in tool_result["citations"]:
                    scoped = conn.execute("SELECT d.knowledge_base_id,c.content FROM document_chunks c "
                                          "JOIN documents d ON d.id=c.document_id WHERE c.id=%s",
                                          (citation["chunk_id"],)).fetchone()
                    assert str(scoped[0]) == kb_a and scoped[1] == citation["content"]
            # Printed values are from this run; no performance claim is made from one request.
            print({"async_upload_ms": round(upload_ms, 2),
                   "queue_wait_ms": round((job[2]-job[4]).total_seconds()*1000, 2),
                   "worker_ms": round((job[3]-job[2]).total_seconds()*1000, 2),
                   "rag_ms": result.get("debug", {}).get("latency_ms", {}).get("total")})
        finally:
            assert client.delete(f"{api}/knowledge-bases/{kb_a}", headers=admin).status_code == 204
            assert client.delete(f"{api}/knowledge-bases/{kb_b}", headers=admin).status_code == 204
            with psycopg.connect(database) as conn:
                conn.execute("DELETE FROM users WHERE id=ANY(%s::uuid[])", (created_users,))
