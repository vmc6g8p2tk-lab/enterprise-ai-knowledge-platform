import os
from pathlib import Path

from app.evaluate import DEFAULT_CORPUS, TimedEmbedding, compute_metrics, load_cases, run_evaluation
from app.providers import ExtractiveAnswer, HashEmbedding
from app.rag import RetrievalConfig


def test_corpus_has_twenty_source_backed_questions():
    cases, documents = load_cases(DEFAULT_CORPUS)
    assert len([case for case in cases if not case.get("insufficient")]) == 20
    assert len([case for case in cases if case.get("insufficient")]) == 4
    assert len(documents) == 5
    assert all(Path(path).is_file() for path in documents.values())


def test_timed_embedding_preserves_provider_identity_and_batch_size():
    wrapped = TimedEmbedding(HashEmbedding(64))
    assert wrapped.provider_name == "hash"
    assert wrapped.model_id == "hash-v1"
    assert wrapped.dimensions == 64
    assert len(wrapped.embed_batch(["one", "two"])) == 2
    assert wrapped.calls[0]["texts"] == 2
    assert wrapped.calls[0]["elapsed_ms"] >= 0


def test_metric_denominators_and_reciprocal_rank():
    rows = [
        {"insufficient": False, "hit": True, "recall": 1.0, "reciprocal_rank": 0.5,
         "citation_checks": [{"exists": True, "in_scope": True}],
         "expected_document_cited": True, "abstained": False},
        {"insufficient": False, "hit": False, "recall": 0.0, "reciprocal_rank": 0.0,
         "citation_checks": [], "expected_document_cited": False, "abstained": True},
        {"insufficient": True, "citation_checks": [], "refusal_correct": True},
    ]
    metrics = compute_metrics(rows)
    assert metrics["hit_at_k"] == 0.5
    assert metrics["recall_at_k"] == 0.5
    assert metrics["mrr"] == 0.25
    assert metrics["citation_exists_rate"] == 1.0
    assert metrics["expected_document_citation_rate"] == 0.5
    assert metrics["insufficient_refusal_rate"] == 1.0


def test_runner_against_real_pgvector():
    # This test intentionally fails without the integration database.
    result = run_evaluation(DEFAULT_CORPUS, os.environ["TEST_DATABASE_URL"],
                            HashEmbedding(), ExtractiveAnswer(), RetrievalConfig())
    assert result["metrics"]["positive_cases"] == 20
    assert result["metrics"]["insufficient_cases"] == 4
    assert len(result["cases"]) == 24
    assert result["corpus_sha256"]
