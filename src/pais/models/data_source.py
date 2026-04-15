"""Data sources (`/control/data-sources`)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field

from pais.models.common import PaisModel


class DataSourceType(str, Enum):
    GOOGLE_DRIVE = "GOOGLE_DRIVE"
    LOCAL_FILES = "LOCAL_FILES"
    HTTP = "HTTP"


class DataSource(PaisModel):
    id: str
    object: Literal["data_source"] = "data_source"
    created_at: int
    name: str
    description: str | None = None
    type: DataSourceType
    origin_url: str | None = None
    credentials: dict[str, Any] | None = None


class DataSourceCreate(PaisModel):
    name: str
    description: str | None = None
    type: DataSourceType
    origin_url: str | None = None
    credentials: dict[str, Any] | None = Field(default=None)
