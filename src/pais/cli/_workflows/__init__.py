"""Ordered registry of v0.6.0 workflows. Order matters — most-common first
so the landing screen surfaces them in the right priority."""

from __future__ import annotations

from pais.cli._workflows._base import Workflow
from pais.cli._workflows.bootstrap_toml import WORKFLOW as BOOTSTRAP_TOML
from pais.cli._workflows.chat import WORKFLOW as CHAT
from pais.cli._workflows.cleanup import WORKFLOW as CLEANUP
from pais.cli._workflows.ingest_data import WORKFLOW as INGEST_DATA
from pais.cli._workflows.search import WORKFLOW as SEARCH
from pais.cli._workflows.setup_agent import WORKFLOW as SETUP_AGENT
from pais.cli._workflows.setup_kb import WORKFLOW as SETUP_KB

WORKFLOWS: list[Workflow] = [
    SETUP_AGENT,  # A — primary use case
    SETUP_KB,  # C — provision without an agent
    BOOTSTRAP_TOML,  # D — apply pending TOML
    INGEST_DATA,  # B — feed data
    CHAT,  # E — talk to an agent
    SEARCH,  # F — raw search
    CLEANUP,  # G — destructive (last on purpose)
]

__all__ = ["WORKFLOWS", "Workflow"]
