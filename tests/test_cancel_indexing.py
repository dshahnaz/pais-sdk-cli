"""Cancel-indexing strategies."""

from __future__ import annotations

import pytest

from pais.client import PaisClient
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


def _provision_running_index(client: PaisClient, store: Store) -> tuple[str, str]:
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb"))
    ix = client.indexes.create(kb.id, IndexCreate(name="ix", embeddings_model_endpoint="bge"))
    client.indexes.trigger_indexing(kb.id, ix.id)
    # Mock marks DONE immediately; force PENDING so cancel has something to do.
    store._kbs[kb.id].indexes[ix.id].active_indexing.state = "PENDING"
    return kb.id, ix.id


def test_cancel_api_strategy_calls_delete_active_indexing() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, ix_id = _provision_running_index(c, store)

    res = c.indexes.cancel_indexing(kb_id, ix_id, strategy="api")
    assert res.cancelled
    assert res.strategy_used == "api"
    assert c.indexes.get_active_indexing(kb_id, ix_id) is None


def test_cancel_recreate_strategy_replaces_index() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, ix_id = _provision_running_index(c, store)

    res = c.indexes.cancel_indexing(kb_id, ix_id, strategy="recreate")
    assert res.cancelled
    assert res.strategy_used == "recreate"
    assert res.new_index_id and res.new_index_id != ix_id


def test_cancel_auto_falls_back_to_recreate_when_endpoint_disabled() -> None:
    store = Store()
    store.disabled_endpoints.add(("DELETE", "/active-indexing"))
    c = PaisClient(FakeTransport(store))
    kb_id, ix_id = _provision_running_index(c, store)

    res = c.indexes.cancel_indexing(kb_id, ix_id, strategy="auto")
    assert res.cancelled
    assert res.strategy_used == "recreate"
    assert res.new_index_id


def test_cancel_api_strategy_raises_when_endpoint_disabled() -> None:
    store = Store()
    store.disabled_endpoints.add(("DELETE", "/active-indexing"))
    c = PaisClient(FakeTransport(store))
    kb_id, ix_id = _provision_running_index(c, store)

    with pytest.raises(NotImplementedError, match="Use --strategy recreate"):
        c.indexes.cancel_indexing(kb_id, ix_id, strategy="api")


def test_cancel_noop_when_no_active_indexing() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb = c.knowledge_bases.create(KnowledgeBaseCreate(name="kb"))
    ix = c.indexes.create(kb.id, IndexCreate(name="ix", embeddings_model_endpoint="bge"))

    res = c.indexes.cancel_indexing(kb.id, ix.id, strategy="auto")
    assert res.cancelled is False
    assert res.strategy_used == "noop"


def test_cancel_noop_when_indexing_already_terminal() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, ix_id = _provision_running_index(c, store)
    # Force terminal state
    store._kbs[kb_id].indexes[ix_id].active_indexing.state = "DONE"

    res = c.indexes.cancel_indexing(kb_id, ix_id, strategy="auto")
    assert res.cancelled is False
    assert res.strategy_used == "noop"
