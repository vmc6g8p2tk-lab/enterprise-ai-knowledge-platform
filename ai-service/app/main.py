import hmac
import os
import re

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .providers import providers
from .agent import AgentForbidden, AgentInputError, planner_from_environment, run_agent
from .metrics import METRICS
from .rag import chat, database_url, ensure_embedding_profile, process_document


app = FastAPI(title="Enterprise AI Knowledge Platform: AI Service", docs_url=None, redoc_url=None)
EMBEDDER, ANSWERER = providers()
PLANNER = planner_from_environment()
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ChatInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AgentInput(ChatInput):
    user_id: str
    request_id: str | None = None


def require_internal(x_internal_token: str | None = Header(default=None)) -> None:
    expected = os.getenv("INTERNAL_SERVICE_TOKEN", "")
    if len(expected) < 32 or not x_internal_token or not hmac.compare_digest(expected, x_internal_token):
        raise HTTPException(status_code=403, detail="internal access denied")


@app.get("/healthz")
def health() -> dict:
    try:
        with psycopg.connect(database_url(), connect_timeout=2) as conn:
            conn.execute("SELECT 1")
            ensure_embedding_profile(conn, EMBEDDER)
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")


@app.get("/metrics", response_class=PlainTextResponse)
def metrics() -> str:
    return METRICS.render()


@app.post("/internal/documents/{document_id}/process", dependencies=[Depends(require_internal)])
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


@app.post("/internal/knowledge-bases/{knowledge_base_id}/chat", dependencies=[Depends(require_internal)])
def answer(knowledge_base_id: str, payload: ChatInput) -> dict:
    if not UUID.fullmatch(knowledge_base_id):
        raise HTTPException(status_code=400, detail="invalid knowledge base id")
    try:
        return chat(knowledge_base_id, payload.question.strip(), EMBEDDER, ANSWERER)
    except Exception:
        raise HTTPException(status_code=500, detail="RAG request failed")


@app.post("/internal/knowledge-bases/{knowledge_base_id}/agent", dependencies=[Depends(require_internal)])
def agent_answer(knowledge_base_id: str, payload: AgentInput) -> dict:
    try:
        return run_agent(knowledge_base_id, payload.user_id, payload.question.strip(),
                         EMBEDDER, ANSWERER, PLANNER, payload.request_id)
    except AgentForbidden:
        raise HTTPException(status_code=403, detail="knowledge base or document unavailable")
    except AgentInputError:
        raise HTTPException(status_code=422, detail="invalid agent tool plan or input")
    except Exception:
        raise HTTPException(status_code=500, detail="agent request failed")
