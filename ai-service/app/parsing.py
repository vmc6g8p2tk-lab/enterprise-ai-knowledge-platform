from pathlib import Path

from docx import Document
from pypdf import PdfReader


SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown"}


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("unsupported document type")
    if suffix == ".pdf":
        text = "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    elif suffix == ".docx":
        text = "\n\n".join(p.text for p in Document(path).paragraphs)
    else:
        text = path.read_text(encoding="utf-8-sig")
    # Keep paragraph boundaries, while normalizing line endings and excess spaces.
    lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        raise ValueError("document has no extractable text; scanned PDFs need OCR")
    return cleaned
