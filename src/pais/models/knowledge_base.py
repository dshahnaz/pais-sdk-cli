"""Knowledge Bases (`/control/knowledge-bases`)."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pais.models.common import PaisModel


class IndexRefreshPolicyType(str, Enum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"


class DataOriginType(str, Enum):
    LOCAL_FILES = "LOCAL_FILES"
    DATA_SOURCE = "DATA_SOURCE"


class IndexRefreshPolicy(PaisModel):
    policy_type: IndexRefreshPolicyType = IndexRefreshPolicyType.MANUAL
    cron_expression: str | None = None


class KnowledgeBase(PaisModel):
    id: str
    object: Literal["knowledge_base"] = "knowledge_base"
    created_at: int
    name: str
    description: str | None = None
    data_origin_type: DataOriginType = DataOriginType.LOCAL_FILES
    index_refresh_policy: IndexRefreshPolicy = IndexRefreshPolicy()


class KnowledgeBaseCreate(PaisModel):
    name: str
    description: str | None = None
    data_origin_type: DataOriginType = DataOriginType.LOCAL_FILES
    index_refresh_policy: IndexRefreshPolicy = IndexRefreshPolicy()


class KnowledgeBaseUpdate(PaisModel):
    name: str | None = None
    description: str | None = None
    index_refresh_policy: IndexRefreshPolicy | None = None
