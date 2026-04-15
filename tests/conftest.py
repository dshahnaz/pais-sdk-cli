"""Shared fixtures: fake-transport client and live-HTTP client against the mock."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from pais.client import PaisClient
from pais.transport.fake_transport import FakeTransport
from pais.transport.httpx_transport import HttpxTransport
from pais_mock.server import build_app
from pais_mock.state import Store


def _find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ServerThread(threading.Thread):
    def __init__(self, app, host: str, port: int) -> None:
        super().__init__(daemon=True)
        config = uvicorn.Config(app=app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(config)

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


@pytest.fixture
def store() -> Store:
    return Store()


@pytest.fixture
def fake_client(store: Store) -> Iterator[PaisClient]:
    client = PaisClient(FakeTransport(store))
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def live_http_client(store: Store) -> Iterator[PaisClient]:
    """PaisClient wired to a real uvicorn running the mock app."""
    app = build_app(store)
    host = "127.0.0.1"
    port = _find_free_port()
    thread = _ServerThread(app, host, port)
    thread.start()
    # Wait until the server is accepting connections.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    else:  # pragma: no cover
        thread.stop()
        raise RuntimeError("mock server failed to start")

    transport = HttpxTransport(
        f"http://{host}:{port}/api/v1",
        retry_max_attempts=1,
        retry_base_delay=0.0,
        chat_cold_start_retries=1,
        chat_cold_start_delay=0.0,
    )
    client = PaisClient(transport)
    try:
        yield client
    finally:
        client.close()
        thread.stop()
        thread.join(timeout=2.0)


@pytest.fixture(params=["fake", "http"])
def any_client(
    request: pytest.FixtureRequest,
    store: Store,
) -> Iterator[PaisClient]:
    """Parametrized client — runs each test against both fake and live HTTP mock."""
    if request.param == "fake":
        client = PaisClient(FakeTransport(store))
        try:
            yield client
        finally:
            client.close()
        return

    # Live HTTP path
    app = build_app(store)
    host = "127.0.0.1"
    port = _find_free_port()
    thread = _ServerThread(app, host, port)
    thread.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    else:  # pragma: no cover
        thread.stop()
        raise RuntimeError("mock server failed to start")

    transport = HttpxTransport(
        f"http://{host}:{port}/api/v1",
        retry_max_attempts=1,
        retry_base_delay=0.0,
        chat_cold_start_retries=1,
        chat_cold_start_delay=0.0,
    )
    client = PaisClient(transport)
    try:
        yield client
    finally:
        client.close()
        thread.stop()
        thread.join(timeout=2.0)
