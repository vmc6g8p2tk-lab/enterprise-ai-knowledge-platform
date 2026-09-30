"""Full Compose API test. Uses the live stack's database; no mocks or skips."""
import os
import time
import uuid

import httpx
import psycopg
from docx import Document
from reportlab.pdfgen.canvas import Canvas


def _session(client: httpx.Client, api: str) -> None:
    email = f"e2e-{uuid.uuid4()}@example.test"
    password = "E2E-password-2026!"
    created = client.post(f"{api}/auth/register", json={"email": email, "password": password})
    assert created.status_code == 201, created.text
    logged = client.post(f"{api}/auth/login", json={"email": email, "password": password})
    assert logged.status_code == 200, logged.text
    client.headers["Authorization"] = f"Bearer {logged.json()['access_token']}"


def _wait_document(client: httpx.Client, api: str, kb_id: str, document_id: str,
                   terminal: str = "ready", timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"{api}/knowledge-bases/{kb_id}/documents")
        assert response.status_code == 200, response.text
        document = next(row for row in response.json() if row["id"] == document_id)
        if document["status"] in {"ready", "failed"}:
            assert document["status"] == terminal, document
            return document
        time.sleep(0.5)
    raise AssertionError(f"document {document_id} did not reach {terminal}")


def test_public_api_upload_chat_and_database_citation():
    api = os.environ["E2E_API_URL"].rstrip("/")
    database = os.environ.get("E2E_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    with httpx.Client(timeout=120) as client:
        _session(client, api)
        health = client.get(f"{api}/healthz")
        assert health.status_code == 200
        first = client.post(f"{api}/knowledge-bases", json={"name": "E2E handbook A"})
        second = client.post(f"{api}/knowledge-bases", json={"name": "E2E handbook B"})
        assert first.status_code == second.status_code == 201
        first_id, second_id = first.json()["id"], second.json()["id"]
        try:
            text = ("Annual leave policy: staff receive twenty days of paid vacation each year. " * 12 +
                    "\n\nSecurity policy: all staff must use two factor authentication. " * 12)
            uploaded = client.post(f"{api}/knowledge-bases/{first_id}/documents", files={"file": ("handbook.txt", text.encode(), "text/plain")})
            assert uploaded.status_code == 202, uploaded.text
            document = _wait_document(client, api, first_id, uploaded.json()["id"])
            assert document["chunk_count"] > 1
            listed = client.get(f"{api}/knowledge-bases/{first_id}/documents")
            assert listed.status_code == 200 and any(row["id"] == document["id"] for row in listed.json())
            response = client.post(f"{api}/knowledge-bases/{first_id}/chat", json={"question": "How many days of vacation does the annual leave policy give staff?"})
            assert response.status_code == 200, response.text
            result = response.json()
            assert any(text in result["answer"].lower() for text in ("twenty days", "20 days"))
            assert result["retrieval"]["initial_top_k"]
            assert result["retrieval"]["reranked"]
            assert result["citations"]
            assert result["debug"]["query"] == "How many days of vacation does the annual leave policy give staff?"
            assert result["debug"]["retrieved_chunks"]
            assert "similarity" in result["debug"]["retrieved_chunks"][0]
            assert result["debug"]["context_chunks"]
            assert result["debug"]["context_chars"] > 0
            assert result["debug"]["latency_ms"]["total"] >= 0
            citation = result["citations"][0]
            assert citation["document_id"] == document["id"]
            assert citation["document_name"] == "handbook.txt"
            with psycopg.connect(database) as conn:
                row = conn.execute("SELECT c.document_id,c.chunk_index,c.content,vector_dims(c.embedding) FROM document_chunks c WHERE c.id=%s", (citation["chunk_id"],)).fetchone()
                profile_dimensions = conn.execute(
                    "SELECT dimensions FROM embedding_profile WHERE singleton=true").fetchone()[0]
                assert (str(row[0]), *row[1:]) == (
                    document["id"], citation["chunk_index"], citation["content"], profile_dimensions)
                assert conn.execute("SELECT count(*) FROM document_chunks WHERE document_id=%s", (document["id"],)).fetchone()[0] == document["chunk_count"]
            isolated = client.post(f"{api}/knowledge-bases/{second_id}/chat", json={"question": "How many days of vacation?"})
            assert isolated.status_code == 200
            assert isolated.json()["citations"] == []
            assert isolated.json()["retrieval"]["initial_top_k"] == []
            assert isolated.json()["abstained"] is True
        finally:
            assert client.delete(f"{api}/knowledge-bases/{first_id}").status_code == 204
            assert client.delete(f"{api}/knowledge-bases/{second_id}").status_code == 204


def test_pdf_docx_markdown_upload_and_failed_status(tmp_path):
    api = os.environ["E2E_API_URL"].rstrip("/")
    database = os.environ.get("E2E_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    pdf = tmp_path / "handbook.pdf"
    canvas = Canvas(str(pdf)); canvas.drawString(72, 750, "PDF travel policy reimburses train tickets."); canvas.save()
    docx = tmp_path / "handbook.docx"
    word = Document(); word.add_paragraph("DOCX travel policy reimburses train tickets."); word.save(docx)
    markdown = tmp_path / "handbook.md"
    markdown.write_text("# Travel policy\n\nMarkdown travel policy reimburses train tickets.", encoding="utf-8")
    blank = tmp_path / "blank.txt"
    blank.write_text("   \n  ", encoding="utf-8")
    with httpx.Client(timeout=120) as client:
        _session(client, api)
        created = client.post(f"{api}/knowledge-bases", json={"name": "E2E format checks"})
        assert created.status_code == 201
        kb_id = created.json()["id"]
        try:
            for path in (pdf, docx, markdown):
                with path.open("rb") as source:
                    result = client.post(f"{api}/knowledge-bases/{kb_id}/documents", files={"file": (path.name, source)})
                assert result.status_code == 202, result.text
                completed = _wait_document(client, api, kb_id, result.json()["id"])
                assert completed["chunk_count"] >= 1
            with blank.open("rb") as source:
                failed = client.post(f"{api}/knowledge-bases/{kb_id}/documents", files={"file": (blank.name, source)})
            assert failed.status_code == 202
            terminal = _wait_document(client, api, kb_id, failed.json()["id"], "failed")
            assert "no extractable text" in terminal["failure_reason"]
            # The document may briefly be failed during a retry; assert the durable job terminal state.
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                with psycopg.connect(database) as conn:
                    job = conn.execute("SELECT status,attempt_count,last_error,started_at,finished_at "
                                       "FROM processing_jobs WHERE document_id=%s", (failed.json()["id"],)).fetchone()
                if job[0] == "failed":
                    break
                time.sleep(0.5)
            assert job[0] == "failed" and job[1] == 3 and job[2] and job[3] and job[4]
        finally:
            assert client.delete(f"{api}/knowledge-bases/{kb_id}").status_code == 204
