"""Sequential local-model latency and fixture-specific citation review.

This uses the unchanged public 20+4 corpus. It is deliberately not a load test.
"""

import argparse
import hashlib
import json
import math
import os
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg


CORPUS = Path("/evaluation/corpus")
CASES = Path("/evaluation/cases.json")


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)

    def percentile(p: float) -> float:
        position = (len(ordered) - 1) * p
        low = math.floor(position)
        return ordered[low] + (ordered[math.ceil(position)] - ordered[low]) * (position - low)

    return {"count": len(values), "median": round(statistics.median(ordered), 2),
            "p95": round(percentile(.95), 2), "p99": round(percentile(.99), 2),
            "min": round(ordered[0], 2), "max": round(ordered[-1], 2)}


def summarize(cases: list[dict], fields: dict[str, str]) -> dict:
    return {label: distribution([case["timing_ms"].get(key, 0) for case in cases])
            for label, key in fields.items()}


def citation_contains_expected_reference(expected_reference: str | None, content: str) -> bool:
    """Narrow regression oracle for the public fact fixture, not semantic entailment."""
    return bool(expected_reference and expected_reference in content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    api = os.environ["E2E_API_URL"].rstrip("/")
    database = os.environ["DATABASE_URL"]
    fixture = json.loads(CASES.read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    for path in sorted(CORPUS.iterdir()):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    digest.update(CASES.read_bytes())
    email = f"phase4-{uuid.uuid4()}@example.test"
    password = "Public-fixture-only-2026!"
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "corpus_sha256": digest.hexdigest(),
              "embedding_model": "bge-m3:567m-fp16", "llm_model": "qwen2.5:7b-instruct-q4_K_M",
              "config": {"top_k": 8, "rerank_k": 4, "context_chars": 3200,
                         "requests_sequential": True, "warmup_excluded": True},
              "rag_cases": [], "agent_cases": []}
    with httpx.Client(timeout=300) as client:
        user_id = kb_id = None
        try:
            response = client.post(f"{api}/auth/register", json={"email": email, "password": password})
            assert response.status_code == 201, response.text
            user_id = response.json()["id"]
            response = client.post(f"{api}/auth/login", json={"email": email, "password": password})
            assert response.status_code == 200, response.text
            headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
            response = client.post(f"{api}/knowledge-bases", headers=headers,
                                   json={"name": "Phase4 public sequential validation"})
            assert response.status_code == 201, response.text
            kb_id = response.json()["id"]
            document_ids = {}
            for path in sorted(CORPUS.iterdir()):
                response = client.post(f"{api}/knowledge-bases/{kb_id}/documents", headers=headers,
                                       files={"file": (path.name, path.read_bytes())})
                assert response.status_code == 202, response.text
                document_ids[path.name] = response.json()["id"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                response = client.get(f"{api}/knowledge-bases/{kb_id}/documents", headers=headers)
                assert response.status_code == 200, response.text
                documents = response.json()
                if all(item["status"] == "ready" for item in documents) and len(documents) == len(document_ids):
                    break
                assert not any(item["status"] == "failed" for item in documents), documents
                time.sleep(.5)
            else:
                raise TimeoutError("document ingestion did not finish")
            report["documents"] = [{"name": item["original_filename"], "chunks": item["chunk_count"]}
                                   for item in documents]
            positives = [case for case in fixture if not case.get("insufficient")]
            with psycopg.connect(database) as conn:
                # Warm both model operations, then make 30 ordinary RAG requests in order.
                for url, question in (("chat", positives[0]["question"]),
                                      ("agent", positives[0]["question"])):
                    response = client.post(f"{api}/knowledge-bases/{kb_id}/{url}", headers=headers,
                                           json={"question": question})
                    assert response.status_code == 200, response.text
                for index in range(30):
                    case = fixture[index % len(fixture)]
                    started = time.perf_counter()
                    response = client.post(f"{api}/knowledge-bases/{kb_id}/chat", headers=headers,
                                           json={"question": case["question"]})
                    wall_ms = (time.perf_counter() - started) * 1000
                    assert response.status_code == 200, response.text
                    payload = response.json()
                    timing = payload["debug"]["latency_ms"]
                    checks = []
                    for citation in payload["citations"]:
                        row = conn.execute("SELECT d.knowledge_base_id,d.original_filename,c.chunk_index,c.content "
                                           "FROM document_chunks c JOIN documents d ON d.id=c.document_id "
                                           "WHERE c.id=%s", (citation["chunk_id"],)).fetchone()
                        checks.append({"real_in_scope": bool(row and str(row[0]) == kb_id and
                                                              row[1] == citation["document_name"] and
                                                              row[2] == citation["chunk_index"] and
                                                              row[3] == citation["content"]),
                                       # Narrow fixture-specific relevance check, not a general semantic judge.
                                       "contains_expected_reference": bool(row and citation_contains_expected_reference(
                                           case.get("expected_reference"), row[3])),
                                       "document_name": citation["document_name"],
                                       "chunk_index": citation["chunk_index"]})
                    report["rag_cases"].append({"id": case["id"], "question": case["question"],
                                                "answer": payload["answer"], "abstained": payload["abstained"],
                                                "timing_ms": {**timing, "wall": round(wall_ms, 2)},
                                                "usage": payload["debug"].get("provider_usage_tokens"),
                                                "citation_checks": checks})
                agent_questions = [
                    ("search_leave", positives[0]["question"]),
                    ("search_incident", next(c["question"] for c in positives if c["id"] == "P07")),
                    ("search_mfa", next(c["question"] for c in positives if c["id"] == "P05")),
                    ("metadata", f"What is the processing status and chunk count of document {document_ids['people-handbook.md']}?"),
                ]
                for index in range(20):
                    label, question = agent_questions[index % len(agent_questions)]
                    started = time.perf_counter()
                    response = client.post(f"{api}/knowledge-bases/{kb_id}/agent", headers=headers,
                                           json={"question": question})
                    wall_ms = (time.perf_counter() - started) * 1000
                    assert response.status_code == 200, response.text
                    payload = response.json()
                    checks = []
                    for citation in payload["citations"]:
                        if citation.get("chunk_id") is not None:
                            row = conn.execute("SELECT d.knowledge_base_id,c.content FROM document_chunks c "
                                               "JOIN documents d ON d.id=c.document_id WHERE c.id=%s",
                                               (citation["chunk_id"],)).fetchone()
                            checks.append(bool(row and str(row[0]) == kb_id and row[1] == citation["content"]))
                        else:
                            row = conn.execute("SELECT knowledge_base_id FROM documents WHERE id=%s",
                                               (citation["document_id"],)).fetchone()
                            checks.append(bool(row and str(row[0]) == kb_id))
                    report["agent_cases"].append({"id": label, "answer": payload["answer"],
                                                  "abstained": payload["abstained"],
                                                  "timing_ms": {**payload["latency_ms"], "wall": round(wall_ms, 2)},
                                                  "tool_calls": payload["tool_calls"],
                                                  "citation_checks": checks})
            report["rag_latency_ms"] = summarize(report["rag_cases"], {
                "embedding": "embedding", "retrieval_db": "retrieval_db", "rerank": "rerank",
                "llm_generation": "generation", "total_service": "total", "wall": "wall"})
            report["agent_latency_ms"] = summarize(report["agent_cases"], {
                "embedding": "embedding_ms", "retrieval_db": "retrieval_ms", "rerank": "rerank_ms",
                "llm_generation": "llm_ms", "total_service": "total_ms", "wall": "wall"})
            report["quality"] = {
                "rag_citations_real_in_scope": sum(c["real_in_scope"] for case in report["rag_cases"]
                                                   for c in case["citation_checks"]),
                "rag_citation_count": sum(len(case["citation_checks"]) for case in report["rag_cases"]),
                "rag_citations_with_expected_reference": sum(c["contains_expected_reference"]
                                                              for case in report["rag_cases"]
                                                              for c in case["citation_checks"]),
                "agent_citations_real_in_scope": sum(sum(case["citation_checks"])
                                                     for case in report["agent_cases"]),
                "agent_citation_count": sum(len(case["citation_checks"])
                                            for case in report["agent_cases"]),
                "p07_answer_has_quantity_and_unit": [
                    ("30" in case["answer"] and
                     ("minute" in case["answer"].lower() or "分钟" in case["answer"]))
                    for case in report["rag_cases"] if case["id"] == "P07"],
                "agent_incident_has_quantity_and_unit": [
                    ("30" in case["answer"] and
                     ("minute" in case["answer"].lower() or "分钟" in case["answer"]))
                    for case in report["agent_cases"] if case["id"] == "search_incident"],
            }
            report["finished_utc"] = datetime.now(timezone.utc).isoformat()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"rag": report["rag_latency_ms"], "agent": report["agent_latency_ms"],
                              "quality": report["quality"]}, ensure_ascii=False))
        finally:
            if kb_id:
                client.delete(f"{api}/knowledge-bases/{kb_id}", headers=headers)
            if user_id:
                with psycopg.connect(database) as conn:
                    conn.execute("DELETE FROM users WHERE id=%s", (user_id,))


if __name__ == "__main__":
    main()
