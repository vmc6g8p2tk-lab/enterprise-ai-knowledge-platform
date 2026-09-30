import json

import httpx
import pytest

from app.providers import (OpenAIAnswer, OpenAIEmbedding, ProviderError,
                           RetryingJSONClient)


def test_embedding_batches_and_restores_input_order():
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["input"])
        items = [{"index": index, "embedding": [float(index + 1)] * 3}
                 for index in reversed(range(len(body["input"])))]
        return httpx.Response(200, json={"data": items})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    transport = RetryingJSONClient("test-only-key", "https://example.invalid/v1", client=client)
    provider = OpenAIEmbedding("test-only-key", "https://example.invalid/v1", "test-model",
                               dimensions=3, batch_size=2, transport=transport)
    vectors = provider.embed_batch(["one", "two", "three", "four", "five"])
    assert calls == [["one", "two"], ["three", "four"], ["five"]]
    assert vectors == [[1.0] * 3, [2.0] * 3, [1.0] * 3, [2.0] * 3, [1.0] * 3]


def test_retry_429_then_success_and_no_retry_for_401():
    attempts = []

    def respond(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    transport = RetryingJSONClient("secret-test-token", "https://example.invalid/v1",
                                   max_retries=1, client=httpx.Client(transport=httpx.MockTransport(respond)),
                                   sleep=lambda seconds: None)
    assert transport.post("embeddings", {"input": ["hello"]}) == {"ok": True}
    assert len(attempts) == 2
    auth_failure = RetryingJSONClient(
        "secret-test-token", "https://example.invalid/v1", max_retries=2,
        client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(401))),
        sleep=lambda seconds: None)
    with pytest.raises(ProviderError, match="HTTP 401") as error:
        auth_failure.post("embeddings", {})
    assert "secret-test-token" not in str(error.value)


def test_timeout_is_bounded_and_sanitized():
    attempts = []

    def timeout(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("raw connection detail and secret-test-token", request=request)

    transport = RetryingJSONClient("secret-test-token", "https://example.invalid/v1",
                                   max_retries=2, client=httpx.Client(transport=httpx.MockTransport(timeout)),
                                   sleep=lambda seconds: None)
    with pytest.raises(ProviderError, match="timed out") as error:
        transport.post("chat/completions", {})
    assert len(attempts) == 3
    assert "secret-test-token" not in str(error.value)


def test_embedding_dimension_and_llm_citations_are_validated():
    bad_embedding = RetryingJSONClient(
        "test-only-key", "https://example.invalid/v1",
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]}))))
    provider = OpenAIEmbedding("test-only-key", "https://example.invalid/v1", "test-model",
                               dimensions=3, transport=bad_embedding)
    with pytest.raises(ProviderError, match="invalid embeddings"):
        provider.embed_batch(["hello"])

    response = {"choices": [{"message": {"content": json.dumps({
        "answer": "Grounded answer", "citation_numbers": [0, 500, -1, "1", 0],
        "insufficient": False})}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}}
    llm_transport = RetryingJSONClient(
        "test-only-key", "https://example.invalid/v1",
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response))))
    result = OpenAIAnswer("test-only-key", "https://example.invalid/v1", "test-chat",
                          transport=llm_transport).generate(
                              "question", [{"content": "context"}])
    assert result.citation_indices == [0]
    assert result.usage == {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}


def test_llm_answer_without_valid_context_reference_abstains():
    response = {"choices": [{"message": {"content": json.dumps({
        "answer": "An unsupported claim", "citation_numbers": [999],
        "insufficient": False})}}]}
    transport = RetryingJSONClient(
        "test-only-key", "https://example.invalid/v1",
        client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response))))
    result = OpenAIAnswer("test-only-key", "https://example.invalid/v1", "test-chat",
                          transport=transport).generate("question", [{"content": "context"}])
    assert result.abstained
    assert result.citation_indices == []
    assert "unsupported claim" not in result.answer.lower()


def test_llm_optional_sampling_config_is_forwarded():
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "answer": "supported", "citation_numbers": [0], "insufficient": False})}}]})

    transport = RetryingJSONClient("test-only-key", "https://example.invalid/v1",
                                   client=httpx.Client(transport=httpx.MockTransport(respond)))
    provider = OpenAIAnswer("test-only-key", "https://example.invalid/v1", "test-chat",
                            transport=transport, temperature=0.0, seed=42)
    assert provider.model_id.startswith("test-chat@")
    assert provider.generate("question", [{"content": "context"}]).citation_indices == [0]
    assert requests[0]["temperature"] == 0.0
    assert requests[0]["seed"] == 42


def test_quantity_answer_prompt_keeps_source_units_and_conditions():
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "answer": "Within 30 minutes of discovery", "citation_numbers": [0],
            "insufficient": False})}}]})

    transport = RetryingJSONClient("test-only-key", "https://example.invalid/v1",
                                   client=httpx.Client(transport=httpx.MockTransport(respond)))
    provider = OpenAIAnswer("test-only-key", "https://example.invalid/v1", "test-chat",
                            transport=transport)
    result = provider.generate("Within how long must it be reported?", [{
        "content": "A suspected incident must be reported within 30 minutes of discovery."}])
    assert result.answer == "Within 30 minutes of discovery"
    assert result.citation_indices == [0]
    assert "保留资料中的单位和适用条件" in requests[0]["messages"][0]["content"]
