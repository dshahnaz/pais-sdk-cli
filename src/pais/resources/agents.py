"""Agents + chat completions (OpenAI-compat namespace)."""

from __future__ import annotations

from collections.abc import Iterator

from pais.models.agent import (
    Agent,
    AgentCreate,
    AgentUpdate,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from pais.resources._base import Resource


class AgentsResource(Resource[Agent]):
    path = "/compatibility/openai/v1/agents"
    model = Agent

    def create(self, payload: AgentCreate) -> Agent:
        return self._create(payload)

    def update(self, agent_id: str, payload: AgentUpdate) -> Agent:
        return self._update(agent_id, payload)

    def chat(self, agent_id: str, request: ChatCompletionRequest) -> ChatCompletionResponse:
        body = request.model_dump(mode="json", exclude_none=True)
        body["stream"] = False
        raw = self._post_json(f"{self.path}/{agent_id}/chat/completions", json=body)
        return ChatCompletionResponse.model_validate(raw)

    def chat_stream(self, agent_id: str, request: ChatCompletionRequest) -> Iterator[bytes]:
        body = request.model_dump(mode="json", exclude_none=True)
        body["stream"] = True
        yield from self._transport.stream(
            "POST", f"{self.path}/{agent_id}/chat/completions", json=body
        )
