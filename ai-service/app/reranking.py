from abc import ABC, abstractmethod

from .providers import terms


class Reranker(ABC):
    @abstractmethod
    def rerank(self, question: str, candidates: list[dict], limit: int) -> list[dict]: ...


class LexicalReranker(Reranker):
    """Replaceable baseline; a learned cross-encoder can implement the same interface."""

    def rerank(self, question: str, candidates: list[dict], limit: int) -> list[dict]:
        query_terms = terms(question)
        for candidate in candidates:
            content_terms = terms(candidate["content"])
            lexical = len(query_terms & content_terms) / max(1, len(query_terms))
            candidate["rerank_score"] = round(0.7 * lexical + 0.3 * max(0.0, 1.0 - candidate["distance"]), 6)
        return sorted(candidates, key=lambda item: item["rerank_score"], reverse=True)[:limit]
