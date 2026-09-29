import re

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .providers import providers
from .rag import chat, database_url, ensure_embedding_profile, process_document


app = FastAPI(title="Enterprise AI Knowledge Platform: AI Service", docs_url=None, redoc_url=None)
EMBEDDER, ANSWERER = providers()
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ChatInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


@app.get("/healthz")
def health() -> dict:
    try:
        with psycopg.connect(database_url(), connect_timeout=2) as conn:
            conn.execute("SELECT 1")
            ensure_embedding_profile(conn, EMBEDDER)
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")


@app.post("/internal/documents/{document_id}/process")
def process(document_id: str) -> dict:
    if not UUID.fullmatch(document_id):
        raise HTTPException(status_code=400, detail="invalid document id")
    try:
        count = process_document(document_id, EMBEDDER)
        return {"chunk_count": count}
    except LookupError:
        raise HTTPException(status_code=404, detail="document not found")
    except Exception:
        raise HTTPException(status_code=422, detail="document processing failed; see document status")


@app.post("/internal/knowledge-bases/{knowledge_base_id}/chat")
def answer(knowledge_base_id: str, payload: ChatInput) -> dict:
    if not UUID.fullmatch(knowledge_base_id):
        raise HTTPException(status_code=400, detail="invalid knowledge base id")
    try:
        return chat(knowledge_base_id, payload.question.strip(), EMBEDDER, ANSWERER)
    except Exception:
        raise HTTPException(status_code=500, detail="RAG request failed")
