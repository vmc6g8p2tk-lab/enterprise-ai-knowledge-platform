"""Repeatable, corpus-grounded RAG evaluation; no model-quality claim."""

import argparse
import hashlib
import json
import os
import uuid
from pathlib import Path

import psycopg

from .providers import AnswerProvider, EmbeddingProvider, providers
from .rag import RetrievalConfig, chat, process_document


DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "docs" / "evaluation"


def load_cases(directory: Path) -> tuple[list[dict], dict[str, Path]]:
    cases = json.loads((directory / "cases.json").read_text(encoding="utf-8"))
    if not isinstance(cases, list) or len(cases) < 20:
        raise ValueError("evaluation needs at least 20 cases")
    documents = {path.name: path for path in (directory / "corpus").glob("*.md")}
    ids: set[str] = set()
    positives = 0
    for case in cases:
        if case["id"] in ids or not case.get("question"):
            raise ValueError("duplicate id or empty question")
        ids.add(case["id"])
        if case.get("insufficient", False):
            continue
        positives += 1
        name, reference = case["expected_document"], case["expected_reference"]
        if name not in documents or reference not in documents[name].read_text(encoding="utf-8"):
            raise ValueError(f"{case['id']} has no source-backed expected reference")
    if positives < 20:
        raise ValueError("evaluation needs at least 20 source-backed questions")
    return cases, documents


def corpus_sha256(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted([directory / "cases.json", *(directory / "corpus").glob("*.md")]):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def compute_metrics(rows: list[dict]) -> dict:
    positives = [row for row in rows if not row["insufficient"]]
    negatives = [row for row in rows if row["insufficient"]]
    if not positives or not negatives:
        raise ValueError("need positive and insufficient cases")
    citations = [citation for row in rows for citation in row["citation_checks"]]
    return {
        "positive_cases": len(positives),
        "insufficient_cases": len(negatives),
        "hit_at_k": sum(row["hit"] for row in positives) / len(positives),
        "recall_at_k": sum(row["recall"] for row in positives) / len(positives),
        "mrr": sum(row["reciprocal_rank"] for row in positives) / len(positives),
        "citation_count": len(citations),
        "citation_exists_rate": (sum(c["exists"] for c in citations) / len(citations)
                                 if citations else None),
        "citation_in_scope_rate": (sum(c["in_scope"] for c in citations) / len(citations)
                                   if citations else None),
        "expected_document_citation_rate": (
            sum(row["expected_document_cited"] for row in positives) / len(positives)),
        "positive_non_refusal_rate": sum(not row["abstained"] for row in positives) / len(positives),
        "insufficient_refusal_rate": sum(row["refusal_correct"] for row in negatives) / len(negatives),
    }


def run_evaluation(directory: Path, db_url: str, embedder: EmbeddingProvider,
                   answerer: AnswerProvider, config: RetrievalConfig) -> dict:
    cases, documents = load_cases(directory)
    kb_id = str(uuid.uuid4())
    doc_ids = {name: str(uuid.uuid4()) for name in documents}
    old_upload_root = os.environ.get("UPLOAD_DIR")
    old_database_url = os.environ.get("DATABASE_URL")
    os.environ["UPLOAD_DIR"] = str((directory / "corpus").resolve())
    os.environ["DATABASE_URL"] = db_url
    inserted = False
    try:
        with psycopg.connect(db_url) as conn:
            conn.execute("INSERT INTO knowledge_bases(id,name) VALUES(%s,%s)",
                         (kb_id, "Temporary RAG evaluation corpus"))
            for name, path in documents.items():
                conn.execute(
                    "INSERT INTO documents(id,knowledge_base_id,original_filename,media_type,storage_path) "
                    "VALUES(%s,%s,%s,'text/markdown',%s)",
                    (doc_ids[name], kb_id, name, str(path.resolve())))
        inserted = True
        for document_id in doc_ids.values():
            process_document(document_id, embedder)
        with psycopg.connect(db_url) as conn:
            chunk_rows = conn.execute(
                "SELECT c.id,c.document_id,c.content FROM document_chunks c "
                "JOIN documents d ON d.id=c.document_id WHERE d.knowledge_base_id=%s",
                (kb_id,)).fetchall()
        results = []
        for case in cases:
            response = chat(kb_id, case["question"], embedder, answerer, config=config, debug=True)
            retrieved_ids = [item["chunk_id"] for item in response["retrieval"]["initial_top_k"]]
            insufficient = bool(case.get("insufficient", False))
            expected_ids: set[int] = set()
            if not insufficient:
                expected_document_id = doc_ids[case["expected_document"]]
                expected_ids = {row[0] for row in chunk_rows
                                if str(row[1]) == expected_document_id
                                and case["expected_reference"] in row[2]}
                if not expected_ids:
                    raise ValueError(f"{case['id']} expected reference was lost during chunking")
            hits = expected_ids.intersection(retrieved_ids)
            first_rank = next((index + 1 for index, chunk_id in enumerate(retrieved_ids)
                               if chunk_id in expected_ids), None)
            citation_checks = []
            for citation in response["citations"]:
                with psycopg.connect(db_url) as conn:
                    actual = conn.execute(
                        "SELECT c.document_id,c.chunk_index,c.content,d.knowledge_base_id "
                        "FROM document_chunks c JOIN documents d ON d.id=c.document_id "
                        "WHERE c.id=%s", (citation["chunk_id"],)).fetchone()
                exists = bool(actual and str(actual[0]) == citation["document_id"]
                              and actual[1] == citation["chunk_index"]
                              and actual[2] == citation["content"])
                citation_checks.append({
                    "chunk_id": citation["chunk_id"], "document_name": citation["document_name"],
                    "exists": exists, "in_scope": bool(exists and str(actual[3]) == kb_id),
                })
            results.append({
                "id": case["id"], "question": case["question"], "insufficient": insufficient,
                "expected_document": case.get("expected_document"),
                "expected_chunk_ids": sorted(expected_ids),
                "retrieved_chunk_ids": retrieved_ids,
                "reranked_chunk_ids": [item["chunk_id"] for item in response["retrieval"]["reranked"]],
                "retrieved_scores": [
                    {"chunk_id": item["chunk_id"], "distance": item["distance"],
                     "similarity": item["similarity"]}
                    for item in response["retrieval"]["initial_top_k"]],
                "context_chunk_ids": [item["chunk_id"] for item in response["debug"]["context_chunks"]],
                "context_chars": response["debug"]["context_chars"],
                "provider_usage_tokens": response["debug"]["provider_usage_tokens"],
                "latency_ms": response["debug"]["latency_ms"],
                "answer": response["answer"], "abstained": response["abstained"],
                "citation_checks": citation_checks,
                "hit": bool(hits) if not insufficient else None,
                "recall": len(hits) / len(expected_ids) if expected_ids else None,
                "reciprocal_rank": (1 / first_rank) if first_rank else 0.0,
                "expected_document_cited": (
                    any(citation["document_name"] == case.get("expected_document")
                        for citation in response["citations"]) if not insufficient else None),
                "refusal_correct": (response["abstained"] and not response["citations"]
                                    if insufficient else None),
            })
        return {
            "corpus_sha256": corpus_sha256(directory),
            "embedding_provider": embedder.provider_name,
            "embedding_model": embedder.model_id,
            "answer_provider": answerer.provider_name,
            "config": {"top_k": config.top_k, "rerank_k": config.rerank_k,
                       "max_context_chars": config.max_context_chars},
            "metrics": compute_metrics(results),
            "cases": results,
        }
    finally:
        try:
            if inserted:
                with psycopg.connect(db_url) as conn:
                    conn.execute("DELETE FROM knowledge_bases WHERE id=%s", (kb_id,))
        finally:
            if old_upload_root is None:
                os.environ.pop("UPLOAD_DIR", None)
            else:
                os.environ["UPLOAD_DIR"] = old_upload_root
            if old_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old_database_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Run source-grounded RAG evaluation")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--rerank-k", type=int, default=4)
    parser.add_argument("--context-chars", type=int, default=3200)
    args = parser.parse_args()
    embedder, answerer = providers()
    result = run_evaluation(args.corpus, os.environ["DATABASE_URL"], embedder, answerer,
                            RetrievalConfig(args.top_k, args.rerank_k, args.context_chars))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
