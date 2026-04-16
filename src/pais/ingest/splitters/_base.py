"""Splitter protocol + the upload-ready record it yields."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass
class SplitDoc:
    """One upload-ready chunk produced by a splitter."""

    origin_name: str
    body: bytes
    media_type: str = "text/markdown"
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Splitter(Protocol):
    """Per-index splitter contract.

    Implementations declare a `kind` (registry key) and an `options_model`
    (pydantic schema for the TOML `[splitter]` block). Construction takes the
    parsed options instance.
    """

    kind: ClassVar[str]
    options_model: ClassVar[type[BaseModel]]

    def __init__(self, options: BaseModel) -> None: ...

    def split(self, path: Path) -> Iterator[SplitDoc]:
        """Yield one SplitDoc per output chunk for the given input file."""
        ...

    def group_key(self, path: Path) -> str:
        """Return the slug used by --replace to match origin_name prefixes."""
        ...


class SplitterOptionsBase(BaseModel):
    """Base for splitter option models. Subclasses add fields freely."""

    model_config = {"extra": "forbid"}
