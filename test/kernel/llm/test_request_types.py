"""Tests for request type based LLM request creation."""

from __future__ import annotations

import pytest

from src.app.plugin_system.api.llm_api import (
    create_embedding_request,
    create_llm_request,
    create_rerank_request,
)
from src.kernel.llm import EmbeddingRequest, LLMRequest, ModelSet, RequestType, RerankRequest
from src.kernel.llm.embedding_response import EmbeddingResponse
from src.kernel.llm.model_client import ModelClientRegistry
from src.kernel.llm.rerank_response import RerankResponse


class _MockMultiClient:
    async def create(self, **kwargs):
        del kwargs
        return "ok", None, None

    async def create_embedding(self, **kwargs):
        del kwargs
        return [[0.1, 0.2], [0.3, 0.4]]

    async def create_rerank(self, **kwargs):
        documents = kwargs["documents"]
        return [
            {"index": 1, "score": 0.9, "document": documents[1]},
            {"index": 0, "score": 0.6, "document": documents[0]},
        ]


@pytest.fixture
def model_set() -> ModelSet:
    return [
        {
            "api_provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model_identifier": "test-model",
            "api_key": "sk-test-key",
            "client_type": "openai",
            "max_retry": 1,
            "timeout": 5.0,
            "retry_interval": 0.0,
            "price_in": 0.0,
            "price_out": 0.0,
            "temperature": 0.0,
            "max_tokens": 1024,
            "max_context": 8192,
            "tool_call_compat": False,
            "extra_params": {},
        }
    ]


def test_create_llm_request_default_completions(model_set: ModelSet) -> None:
    request = create_llm_request(model_set=model_set, request_name="chat_test")
    assert isinstance(request, LLMRequest)
    assert request.request_type == RequestType.COMPLETIONS


def test_create_embedding_request(model_set: ModelSet) -> None:
    request = create_embedding_request(
        model_set=model_set,
        request_name="embed_test",
        inputs=["hello", "world"],
    )
    assert isinstance(request, EmbeddingRequest)
    assert request.request_type == RequestType.EMBEDDINGS
    assert request.inputs == ["hello", "world"]


def test_create_rerank_request(model_set: ModelSet) -> None:
    request = create_rerank_request(
        model_set=model_set,
        request_name="rerank_test",
        query="hello",
        documents=["doc1", "doc2"],
        top_n=1,
    )
    assert isinstance(request, RerankRequest)
    assert request.request_type == RequestType.RERANK
    assert request.query == "hello"
    assert request.documents == ["doc1", "doc2"]
    assert request.top_n == 1


@pytest.mark.asyncio
async def test_embedding_request_send(model_set: ModelSet) -> None:
    request = EmbeddingRequest(
        model_set=model_set,
        request_name="embed_send",
        inputs=["hello", "world"],
        clients=ModelClientRegistry(openai=_MockMultiClient()),
    )

    response = await request.send()
    assert isinstance(response, EmbeddingResponse)
    assert response.embeddings == [[0.1, 0.2], [0.3, 0.4]]
    assert response.model_name == "test-model"


@pytest.mark.asyncio
async def test_rerank_request_send(model_set: ModelSet) -> None:
    request = RerankRequest(
        model_set=model_set,
        request_name="rerank_send",
        query="hello",
        documents=["doc1", "doc2"],
        top_n=2,
        clients=ModelClientRegistry(openai=_MockMultiClient()),
    )

    response = await request.send()
    assert isinstance(response, RerankResponse)
    assert len(response.results) == 2
    assert response.results[0].index == 1
    assert response.results[0].score == 0.9
    assert response.results[0].document == "doc2"
