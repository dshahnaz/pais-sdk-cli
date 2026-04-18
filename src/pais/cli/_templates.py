"""Named config templates for `pais kb create` / `pais index create` / `pais agent create`.

Templates are starting points, not lockdowns. The CLI flag `--template <name>`
seeds defaults for any unset flags; explicitly-passed flags always win.

The `field-proven` agent template mirrors the user's working `test.py:341-365`
(see `feedback_pais_agent_undocumented_defaults` memory) and is the recommended
recipe for production PAIS deployments running gpt-oss-120b.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TemplateKind = Literal["kb", "index", "agent"]


@dataclass(frozen=True)
class Template:
    kind: TemplateKind
    name: str
    description: str
    defaults: dict[str, Any] = field(default_factory=dict)


KB_TEMPLATES: list[Template] = [
    Template(
        kind="kb",
        name="local-files-manual",
        description="Local file uploads with manual indexing (recommended for `pais ingest`)",
        defaults={
            "data_origin_type": "LOCAL_FILES",
            "index_refresh_policy_type": "MANUAL",
        },
    ),
    Template(
        kind="kb",
        name="custom",
        description="No presets — caller fills every field",
        defaults={},
    ),
]

INDEX_TEMPLATES: list[Template] = [
    Template(
        kind="index",
        name="test-suite-bge",
        description="chunk=400 / overlap=100 / BAAI/bge-small-en-v1.5 (matches the field-proven test.py recipe)",
        defaults={
            "embeddings_model_endpoint": "BAAI/bge-small-en-v1.5",
            "text_splitting": "SENTENCE",
            "chunk_size": 400,
            "chunk_overlap": 100,
        },
    ),
    Template(
        kind="index",
        name="test-suite-arctic",
        description="chunk=400 / overlap=100 / Snowflake arctic-embed-m-v2.0",
        defaults={
            "embeddings_model_endpoint": "Snowflake/snowflake-arctic-embed-m-v2.0",
            "text_splitting": "SENTENCE",
            "chunk_size": 400,
            "chunk_overlap": 100,
        },
    ),
    Template(
        kind="index",
        name="code-rag",
        description="chunk=800 / overlap=200 / BAAI/bge-small-en-v1.5 (larger chunks for code context)",
        defaults={
            "embeddings_model_endpoint": "BAAI/bge-small-en-v1.5",
            "text_splitting": "SENTENCE",
            "chunk_size": 800,
            "chunk_overlap": 200,
        },
    ),
    Template(
        kind="index",
        name="custom",
        description="No presets — caller fills every field",
        defaults={},
    ),
]

AGENT_TEMPLATES: list[Template] = [
    Template(
        kind="agent",
        name="field-proven",
        description=(
            "Field-proven recipe (recommended) — system-message + structured + assistant "
            "+ session_max_length=10000 + delete_oldest. Mirrors the user's working test.py "
            "and avoids server-default 502s on production deployments."
        ),
        defaults={
            "completion_role": "assistant",
            "session_max_length": 10000,
            "session_summarization_strategy": "delete_oldest",
            "index_reference_format": "structured",
            "chat_system_instruction_mode": "system-message",
            "index_top_n": 5,
            "index_similarity_cutoff": 0.0,
        },
    ),
    Template(
        kind="agent",
        name="minimal",
        description="Only required fields; server picks defaults for the rest. May 502 on production deployments.",
        defaults={},
    ),
    Template(
        kind="agent",
        name="custom",
        description="No presets — caller fills every field",
        defaults={},
    ),
]

_BY_KIND: dict[TemplateKind, list[Template]] = {
    "kb": KB_TEMPLATES,
    "index": INDEX_TEMPLATES,
    "agent": AGENT_TEMPLATES,
}


def list_templates(kind: TemplateKind) -> list[Template]:
    return list(_BY_KIND[kind])


def get_template(kind: TemplateKind, name: str) -> Template:
    """Look up a template by kind + name. Raises ValueError if unknown."""
    for t in _BY_KIND[kind]:
        if t.name == name:
            return t
    valid = ", ".join(t.name for t in _BY_KIND[kind])
    raise ValueError(f"unknown {kind} template '{name}'. valid: {valid}")


def apply_template(kind: TemplateKind, name: str, overrides: dict[str, Any]) -> dict[str, Any]:
    """Resolve template defaults, then apply overrides. Caller-supplied values
    (with non-None values) always win over template seeds.
    """
    template = get_template(kind, name)
    out: dict[str, Any] = dict(template.defaults)
    for key, value in overrides.items():
        if value is not None:
            out[key] = value
    return out
