"""OpenAI-compat endpoints: models, embeddings, chat (non-agent)."""

from __future__ import annotations

from collections.abc import Iterator

from pais.models.agent import ChatCompletionRequest, ChatCompletionResponse
from pais.models.common import ListResponse
from pais.models.openai_compat import EmbeddingRequest, EmbeddingResponse, Model
from pais.resources._base import Resource


class ModelsResource(Resource[Model]):
    path = "/compatibility/openai/v1/models"
    model = Model

    def list(self) -> ListResponse[Model]:  # type: ignore[override]
        raw = self._get_json(self.path)
        return ListResponse[Model].model_validate(raw)


class EmbeddingsResource:
    def __init__(self, transport) -> None:  # type: ignore[no-untyped-def]
        self._transport = transport

    def create(self, request: EmbeddingRequest) -> EmbeddingResponse:
        body = request.model_dump(mode="json", exclude_none=True)
        raw = self._transport.request("POST", "/compatibility/openai/v1/embeddings", json=body).body
        return EmbeddingResponse.model_validate(raw)


class ChatResource:
    """Non-agent chat completions (direct model invocation)."""

    def __init__(self, transport) -> None:  # type: ignore[no-untyped-def]
        self._transport = transport

    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        body = request.model_dump(mode="json", exclude_none=True)
        body["stream"] = False
        raw = self._transport.request(
            "POST", "/compatibility/openai/v1/chat/completions", json=body
        ).body
        return ChatCompletionResponse.model_validate(raw)

    def stream(self, request: ChatCompletionRequest) -> Iterator[bytes]:
        body = request.model_dump(mode="json", exclude_none=True)
        body["stream"] = True
        yield from self._transport.stream(
            "POST", "/compatibility/openai/v1/chat/completions", json=body
        )
