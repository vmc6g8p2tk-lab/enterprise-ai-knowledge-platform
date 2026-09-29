"""Full Compose API test. Requires E2E_API_URL and TEST_DATABASE_URL; no mocks or skips."""
import os

import httpx
import psycopg
from docx import Document
from reportlab.pdfgen.canvas import Canvas


def test_public_api_upload_chat_and_database_citation():
    api = os.environ["E2E_API_URL"].rstrip("/")
    database = os.environ["TEST_DATABASE_URL"]
    with httpx.Client(timeout=120) as client:
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
            assert uploaded.status_code == 201, uploaded.text
            document = uploaded.json()
            assert document["status"] == "ready", document
            assert document["chunk_count"] > 1
            listed = client.get(f"{api}/knowledge-bases/{first_id}/documents")
            assert listed.status_code == 200 and any(row["id"] == document["id"] for row in listed.json())
            response = client.post(f"{api}/knowledge-bases/{first_id}/chat", json={"question": "How many days of vacation does the annual leave policy give staff?"})
            assert response.status_code == 200, response.text
            result = response.json()
            assert "twenty days" in result["answer"].lower()
            assert result["retrieval"]["initial_top_k"]
            assert result["retrieval"]["reranked"]
            assert result["citations"]
            citation = result["citations"][0]
            assert citation["document_id"] == document["id"]
            assert citation["document_name"] == "handbook.txt"
            with psycopg.connect(database) as conn:
                row = conn.execute("SELECT c.document_id,c.chunk_index,c.content,vector_dims(c.embedding) FROM document_chunks c WHERE c.id=%s", (citation["chunk_id"],)).fetchone()
                assert (str(row[0]), *row[1:]) == (document["id"], citation["chunk_index"], citation["content"], 1536)
                assert conn.execute("SELECT count(*) FROM document_chunks WHERE document_id=%s", (document["id"],)).fetchone()[0] == document["chunk_count"]
            isolated = client.post(f"{api}/knowledge-bases/{second_id}/chat", json={"question": "How many days of vacation?"})
            assert isolated.status_code == 200
            assert isolated.json()["citations"] == []
            assert isolated.json()["retrieval"]["initial_top_k"] == []
        finally:
            with psycopg.connect(database) as conn:
                conn.execute("DELETE FROM knowledge_bases WHERE id IN (%s,%s)", (first_id, second_id))


def test_pdf_docx_markdown_upload_and_failed_status(tmp_path):
    api = os.environ["E2E_API_URL"].rstrip("/")
    database = os.environ["TEST_DATABASE_URL"]
    pdf = tmp_path / "handbook.pdf"
    canvas = Canvas(str(pdf)); canvas.drawString(72, 750, "PDF travel policy reimburses train tickets."); canvas.save()
    docx = tmp_path / "handbook.docx"
    word = Document(); word.add_paragraph("DOCX travel policy reimburses train tickets."); word.save(docx)
    markdown = tmp_path / "handbook.md"
    markdown.write_text("# Travel policy\n\nMarkdown travel policy reimburses train tickets.", encoding="utf-8")
    blank = tmp_path / "blank.txt"
    blank.write_text("   \n  ", encoding="utf-8")
    with httpx.Client(timeout=120) as client:
        created = client.post(f"{api}/knowledge-bases", json={"name": "E2E format checks"})
        assert created.status_code == 201
        kb_id = created.json()["id"]
        try:
            for path in (pdf, docx, markdown):
                with path.open("rb") as source:
                    result = client.post(f"{api}/knowledge-bases/{kb_id}/documents", files={"file": (path.name, source)})
                assert result.status_code == 201, result.text
                assert result.json()["status"] == "ready", result.json()
                assert result.json()["chunk_count"] >= 1
            with blank.open("rb") as source:
                failed = client.post(f"{api}/knowledge-bases/{kb_id}/documents", files={"file": (blank.name, source)})
            assert failed.status_code == 201
            assert failed.json()["status"] == "failed"
            assert "no extractable text" in failed.json()["failure_reason"]
        finally:
            with psycopg.connect(database) as conn:
                conn.execute("DELETE FROM knowledge_bases WHERE id=%s", (kb_id,))
