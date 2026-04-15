"""Shared fixtures: fake-transport client and live-HTTP client against the mock."""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import warnings
from collections.abc import Iterator

# Silence HuggingFace hub noise BEFORE any module that imports it, so stdout
# stays clean for CliRunner tests (Click/Typer merges stderr into r.output).
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
for _name in ("huggingface_hub", "huggingface_hub.utils._http"):
    logging.getLogger(_name).setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module=r"huggingface_hub(\..*)?")

import pytest  # noqa: E402
import uvicorn  # noqa: E402

from pais.client import PaisClient  # noqa: E402
from pais.transport.fake_transport import FakeTransport  # noqa: E402
from pais.transport.httpx_transport import HttpxTransport  # noqa: E402
from pais_mock.server import build_app  # noqa: E402
from pais_mock.state import Store  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _warm_tokenizer() -> None:
    """Pre-load the bge-small tokenizer once, before any CliRunner-captured test,
    so the one-time download warning fires outside stdout capture."""
    import contextlib

    with contextlib.suppress(Exception):
        from pais.dev.token_budget import token_count

        token_count("warmup")


@pytest.fixture(autouse=True)
def _reset_stdlib_logging_handlers() -> Iterator[None]:
    """Prevent stdlib logging handlers bound to a stale stderr from leaking
    between tests (they cause 'Logging error' lines that pollute CliRunner
    output when the captured stream has been rotated)."""
    yield
    import contextlib

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()


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
