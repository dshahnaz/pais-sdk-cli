"""PaisClient — the primary SDK entry point.

Two constructors:
  1. `PaisClient(transport=...)` — primary; host apps inject their own Transport.
  2. `PaisClient.from_settings(settings)` — convenience for CLI + scripts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pais.auth.base import AuthStrategy
from pais.auth.bearer import BearerAuth
from pais.auth.none import NoAuth
from pais.auth.oidc_password import OIDCPasswordAuth
from pais.logging import configure_logging
from pais.resources.agents import AgentsResource
from pais.resources.data_sources import DataSourcesResource
from pais.resources.indexes import IndexesResource
from pais.resources.knowledge_bases import KnowledgeBasesResource
from pais.resources.mcp_tools import McpToolsResource
from pais.resources.openai_compat import ChatResource, EmbeddingsResource, ModelsResource
from pais.transport.base import Transport
from pais.transport.httpx_transport import HttpxTransport

if TYPE_CHECKING:
    from pais.config import Settings


class PaisClient:
    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self.data_sources = DataSourcesResource(transport)
        self.knowledge_bases = KnowledgeBasesResource(transport)
        self.indexes = IndexesResource(transport)
        self.mcp_tools = McpToolsResource(transport)
        self.agents = AgentsResource(transport)
        self.models = ModelsResource(transport)
        self.embeddings = EmbeddingsResource(transport)
        self.chat = ChatResource(transport)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> PaisClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- Convenience constructors -------------------------------------------
    @classmethod
    def from_settings(cls, settings: Settings) -> PaisClient:
        configure_logging(
            level=settings.log_level,
            log_file=settings.log_file,
            json_console=settings.log_json_console,
        )
        if settings.mode == "mock":
            from pais.transport.fake_transport import FakeTransport
            from pais_mock.state import Store

            transport: Transport = FakeTransport(Store())
            return cls(transport)

        auth = _build_auth(settings)
        transport = HttpxTransport(
            settings.base_url,
            auth=auth,
            verify_ssl=settings.verify_ssl,
            connect_timeout=settings.connect_timeout,
            read_timeout=settings.read_timeout,
            total_timeout=settings.total_timeout,
            retry_max_attempts=settings.retry_max_attempts,
            retry_base_delay=settings.retry_base_delay,
            retry_max_delay=settings.retry_max_delay,
            chat_cold_start_retries=settings.chat_cold_start_retries,
            chat_cold_start_delay=settings.chat_cold_start_delay,
            chat_retry_on_empty=settings.chat_retry_on_empty,
        )
        return cls(transport)


def _build_auth(settings: Settings) -> AuthStrategy:
    if settings.auth == "none":
        return NoAuth()
    if settings.auth == "bearer":
        tok = settings.bearer_token.get_secret_value() if settings.bearer_token else None
        if not tok:
            raise ValueError("PAIS_AUTH=bearer requires PAIS_BEARER_TOKEN")
        return BearerAuth(tok)
    if settings.auth == "oidc_password":
        missing = [
            k
            for k, v in {
                "PAIS_OIDC_ISSUER": settings.oidc_issuer,
                "PAIS_CLIENT_ID": settings.client_id,
                "PAIS_USERNAME": settings.username,
                "PAIS_PASSWORD": settings.password,
            }.items()
            if not v
        ]
        if missing:
            raise ValueError(f"PAIS_AUTH=oidc_password requires: {', '.join(missing)}")
        assert (
            settings.oidc_issuer and settings.client_id and settings.username and settings.password
        )
        return OIDCPasswordAuth(
            issuer=settings.oidc_issuer,
            client_id=settings.client_id,
            username=settings.username,
            password=settings.password.get_secret_value(),
            client_secret=(
                settings.client_secret.get_secret_value() if settings.client_secret else None
            ),
            cache_path=settings.token_cache_path,
            verify_ssl=settings.verify_ssl,
        )
    raise ValueError(f"Unknown auth mode: {settings.auth}")
