"""Agents + chat completions (`/compatibility/openai/v1/agents/...`)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pais.models.common import PaisModel


class ToolLinkType(str, Enum):
    PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK = "PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK"
    PAIS_MCP_TOOL_LINK = "PAIS_MCP_TOOL_LINK"


class ToolLink(PaisModel):
    link_type: ToolLinkType
    tool_id: str
    top_n: int | None = None
    similarity_cutoff: float | None = None
    server: str | None = None


class Agent(PaisModel):
    id: str
    object: Literal["agent"] = "agent"
    created_at: int
    name: str
    description: str | None = None
    model: str
    instructions: str | None = None
    completion_role: str = "assistant"
    session_max_length: int = 10000
    session_summarization_strategy: str = "delete_oldest"
    index_reference_format: str | None = "structured"
    chat_system_instruction_mode: str = "system-message"
    # Doc-aligned: agent points at an index UUID directly.
    index_id: str | None = None
    index_top_n: int | None = None
    # Legacy: tools=[ToolLink(...)] kept for back-compat with deployments
    # that still wire MCP tools through the agent surface.
    tools: list[ToolLink] = []
    status: str = "READY"


class AgentCreate(PaisModel):
    name: str
    description: str | None = None
    model: str
    instructions: str | None = None
    completion_role: str = "assistant"
    session_max_length: int = 10000
    session_summarization_strategy: str = "delete_oldest"
    index_reference_format: str | None = "structured"
    chat_system_instruction_mode: str = "system-message"
    # Doc-aligned: prefer this for new code.
    index_id: str | None = None
    index_top_n: int | None = None
    # Legacy fallback.
    tools: list[ToolLink] = []


class AgentUpdate(PaisModel):
    name: str | None = None
    description: str | None = None
    model: str | None = None
    instructions: str | None = None
    tools: list[ToolLink] | None = None


class ChatMessage(PaisModel):
    role: str
    content: str | None = None
    name: str | None = None
    tool_call_id: str | None = None


class ChatCompletionRequest(PaisModel):
    messages: list[ChatMessage]
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = 500
    stream: bool = False
    top_p: float | None = None


class ChatCompletionChoice(PaisModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str | None = "stop"


class ChatCompletionUsage(PaisModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(PaisModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage = ChatCompletionUsage()
    references: list[dict[str, Any]] | None = None
