"""Structured logging with request-id context, secret redaction, and rotating file sink."""

from __future__ import annotations

import contextvars
import logging
import logging.handlers
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import structlog
from structlog.typing import EventDict, WrappedLogger


class _LazyStderrLogger:
    """Minimal logger that writes to the *current* sys.stderr each call,
    so pytest's capsys works correctly even across tests."""

    def msg(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    log = debug = info = warning = error = critical = failure = fatal = msg


class _LazyStderrLoggerFactory:
    def __call__(self, *args: Any, **kwargs: Any) -> _LazyStderrLogger:
        return _LazyStderrLogger()


_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pais_request_id", default=None
)

_REDACT_KEYS = {
    "authorization",
    "password",
    "client_secret",
    "api_key",
    "access_token",
    "refresh_token",
    "id_token",
    "x-api-key",
}
_REDACT_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)
_REDACTED = "***"


def new_request_id() -> str:
    rid = uuid.uuid4().hex
    _request_id_var.set(rid)
    return rid


def current_request_id() -> str | None:
    return _request_id_var.get()


def set_request_id(rid: str | None) -> None:
    _request_id_var.set(rid)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _REDACT_RE.sub(rf"\1{_REDACTED}", value)
    if isinstance(value, dict):
        return {
            k: (_REDACTED if k.lower() in _REDACT_KEYS else _redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


def _redact_processor(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict.keys()):
        if key.lower() in _REDACT_KEYS:
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


def _request_id_processor(_logger: WrappedLogger, _name: str, event_dict: EventDict) -> EventDict:
    rid = _request_id_var.get()
    if rid and "request_id" not in event_dict:
        event_dict["request_id"] = rid
    return event_dict


def configure_logging(
    *,
    level: str = "INFO",
    log_file: str | Path | None = None,
    json_console: bool = False,
) -> None:
    """Configure structlog + stdlib logging. Idempotent."""
    lvl = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(lvl)
    # Clear existing handlers so re-configuration is clean in tests.
    for h in list(root.handlers):
        root.removeHandler(h)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
        )
    for h in handlers:
        h.setLevel(lvl)
        root.addHandler(h)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _request_id_processor,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_processor,
    ]
    renderer: Any
    if json_console:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        context_class=dict,
        logger_factory=_LazyStderrLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "pais") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
