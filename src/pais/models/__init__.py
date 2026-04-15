"""Contract-first pydantic models for the PAIS API.

Imported by both the SDK (for request/response validation) and the mock
server (for response generation). The mock serves exactly what the SDK
validates — this is the contract-first guarantee.
"""

from pais.models.agent import (
    Agent,
    AgentCreate,
    AgentUpdate,
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
    ToolLink,
    ToolLinkType,
)
from pais.models.common import ErrorDetailModel, ErrorResponse, ListResponse
from pais.models.data_source import DataSource, DataSourceCreate, DataSourceType
from pais.models.index import (
    Document,
    DocumentState,
    Index,
    IndexCreate,
    Indexing,
    IndexingState,
    IndexStatus,
    IndexUpdate,
    SearchHit,
    SearchQuery,
    SearchResponse,
    TextSplittingKind,
)
from pais.models.knowledge_base import (
    DataOriginType,
    IndexRefreshPolicy,
    IndexRefreshPolicyType,
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
)
from pais.models.mcp import McpTool, McpToolLinkType
from pais.models.openai_compat import (
    EmbeddingData,
    EmbeddingRequest,
    EmbeddingResponse,
    Model,
    ModelEngine,
    ModelType,
)

__all__ = [
    "Agent",
    "AgentCreate",
    "AgentUpdate",
    "ChatCompletionChoice",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatCompletionUsage",
    "ChatMessage",
    "DataOriginType",
    "DataSource",
    "DataSourceCreate",
    "DataSourceType",
    "Document",
    "DocumentState",
    "EmbeddingData",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "ErrorDetailModel",
    "ErrorResponse",
    "Index",
    "IndexCreate",
    "IndexRefreshPolicy",
    "IndexRefreshPolicyType",
    "IndexStatus",
    "IndexUpdate",
    "Indexing",
    "IndexingState",
    "KnowledgeBase",
    "KnowledgeBaseCreate",
    "KnowledgeBaseUpdate",
    "ListResponse",
    "McpTool",
    "McpToolLinkType",
    "Model",
    "ModelEngine",
    "ModelType",
    "SearchHit",
    "SearchQuery",
    "SearchResponse",
    "TextSplittingKind",
    "ToolLink",
    "ToolLinkType",
]
