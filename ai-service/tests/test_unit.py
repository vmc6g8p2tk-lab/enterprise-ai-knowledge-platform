from pathlib import Path

from docx import Document
from reportlab.pdfgen.canvas import Canvas

from app.chunking import chunk_text
from app.parsing import extract_text
from app.providers import ExtractiveAnswer, HashEmbedding
from app.reranking import LexicalReranker


def test_parse_txt_markdown_docx_pdf(tmp_path: Path):
    txt = tmp_path / "sample.txt"
    txt.write_text("Policy one.\n\nPolicy two.", encoding="utf-8")
    md = tmp_path / "sample.md"
    md.write_text("# Handbook\n\nThe policy is here.", encoding="utf-8")
    docx_path = tmp_path / "sample.docx"
    doc = Document(); doc.add_paragraph("DOCX handbook text"); doc.save(docx_path)
    pdf_path = tmp_path / "sample.pdf"
    canvas = Canvas(str(pdf_path)); canvas.drawString(72, 750, "PDF handbook text"); canvas.save()
    assert "Policy two" in extract_text(txt)
    assert "Handbook" in extract_text(md)
    assert "DOCX handbook text" in extract_text(docx_path)
    assert "PDF handbook text" in extract_text(pdf_path)


def test_chunking_respects_boundaries_and_overlap():
    text = "\n\n".join(f"Section {i}. " + (f"policy{i} applies here. " * 25) for i in range(5))
    chunks = chunk_text(text, max_chars=300, overlap_chars=40)
    assert len(chunks) > 5
    assert all(0 < len(chunk) <= 300 for chunk in chunks)
    assert chunks[0] != chunks[1]
    assert any(word in chunks[0] and word in chunks[1] for word in chunks[0].split()[-5:])


def test_dev_providers_are_deterministic_and_grounded():
    embedder = HashEmbedding()
    assert len(embedder.embed("vacation policy")) == 1536
    assert embedder.embed("vacation policy") == embedder.embed("vacation policy")
    answer, indices = ExtractiveAnswer().answer("What is the vacation policy?", [{"content": "The vacation policy grants ten days."}])
    assert "ten days" in answer and indices == [0]
    answer, indices = ExtractiveAnswer().answer("quantum entanglement", [{"content": "The vacation policy grants ten days."}])
    assert "资料不足" in answer and indices == []


def test_reranker_interface_orders_lexical_match():
    rows = [{"content": "vacation policy grants leave", "distance": 0.4},
            {"content": "security key rotation", "distance": 0.1}]
    ranked = LexicalReranker().rerank("vacation leave", rows, 2)
    assert ranked[0]["content"].startswith("vacation")
    assert ranked[0]["rerank_score"] > ranked[1]["rerank_score"]
