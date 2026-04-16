"""Typed view of `[profiles.<name>.knowledge_bases.*]` blocks in pais.toml.

Settings consumes the connection-level keys; this module covers the
declarative KB/index/splitter blocks consumed by the alias resolver and
`pais kb ensure`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Allowed alias shape — must not collide with UUID4 (`_alias.UUID_RE`).
_ALIAS_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]*$"


class IndexRefreshPolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_type: str = "MANUAL"
    cron_expression: str | None = None


class SplitterConfig(BaseModel):
    """Per-index splitter declaration. `kind` selects the registered Splitter;
    everything else is the splitter's options model (validated separately)."""

    model_config = ConfigDict(extra="allow")
    kind: str

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        from pais.ingest.registry import SPLITTER_REGISTRY

        if v not in SPLITTER_REGISTRY:
            available = ", ".join(sorted(SPLITTER_REGISTRY)) or "(none)"
            raise ValueError(f"unknown splitter kind {v!r}; registered: {available}")
        return v

    def options(self) -> BaseModel:
        """Validate and return a typed options instance for this splitter kind."""
        from pais.ingest.registry import get_splitter

        cls = get_splitter(self.kind)
        # Strip the `kind` field; the rest is the options dict.
        opts = self.model_dump()
        opts.pop("kind", None)
        instance: BaseModel = cls.options_model(**opts)
        return instance


class IndexDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(pattern=_ALIAS_PATTERN)
    name: str = Field(min_length=1)
    description: str | None = None
    embeddings_model_endpoint: str = Field(min_length=1)
    text_splitting: str = "SENTENCE"
    chunk_size: int = Field(default=512, gt=0)
    chunk_overlap: int = Field(default=64, ge=0)
    splitter: SplitterConfig | None = None  # optional; absent → must be passed via --splitter

    @model_validator(mode="after")
    def _overlap_lt_size(self) -> IndexDeclaration:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self


class KnowledgeBaseDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    description: str | None = None
    data_origin_type: str = "LOCAL_FILES"
    index_refresh_policy: IndexRefreshPolicyConfig = IndexRefreshPolicyConfig()
    indexes: list[IndexDeclaration] = Field(default_factory=list)

    @model_validator(mode="after")
    def _alias_unique(self) -> KnowledgeBaseDeclaration:
        seen: set[str] = set()
        for ix in self.indexes:
            if ix.alias in seen:
                raise ValueError(f"duplicate index alias within KB: {ix.alias!r}")
            seen.add(ix.alias)
        return self


class ProfileConfig(BaseModel):
    """The KB/index tree declared under one profile. Connection settings live
    on `Settings`; this is purely the declarative resource block."""

    model_config = ConfigDict(extra="ignore")
    knowledge_bases: dict[str, KnowledgeBaseDeclaration] = Field(default_factory=dict)

    @field_validator("knowledge_bases")
    @classmethod
    def _kb_aliases(
        cls, v: dict[str, KnowledgeBaseDeclaration]
    ) -> dict[str, KnowledgeBaseDeclaration]:
        import re

        for alias in v:
            if not re.match(_ALIAS_PATTERN, alias):
                raise ValueError(
                    f"KB alias {alias!r} must match /{_ALIAS_PATTERN}/ (letter, then letters/digits/_/-)"
                )
        return v


def parse_profile_config(profile_section: dict[str, Any]) -> ProfileConfig:
    """Build a typed ProfileConfig from a parsed `[profiles.X]` section dict."""
    return ProfileConfig.model_validate(profile_section)
