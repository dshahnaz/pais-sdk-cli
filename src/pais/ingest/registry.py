"""Splitter registry. Built-ins register themselves on import.

Uses `Any` for the value type because the Splitter contract is duck-typed
(Protocol) and pydantic options-model variance makes nominal subtype checks
too strict. Runtime correctness is enforced by the `kind`-attribute check
inside `register_splitter`.
"""

from __future__ import annotations

from typing import Any

SPLITTER_REGISTRY: dict[str, Any] = {}


def register_splitter(cls: Any) -> Any:
    """Decorator: register a Splitter subclass under its `kind`."""
    kind = getattr(cls, "kind", None)
    if not kind or not isinstance(kind, str):
        raise TypeError(f"{cls.__name__} must declare a string class attribute 'kind'")
    if kind in SPLITTER_REGISTRY:
        raise ValueError(f"splitter kind {kind!r} already registered")
    SPLITTER_REGISTRY[kind] = cls
    return cls


def get_splitter(kind: str) -> Any:
    """Return the splitter class registered under `kind`."""
    try:
        return SPLITTER_REGISTRY[kind]
    except KeyError:
        available = ", ".join(sorted(SPLITTER_REGISTRY)) or "(none)"
        raise KeyError(f"unknown splitter kind {kind!r}; available: {available}") from None


def _ensure_builtins_loaded() -> None:
    """Import the built-in splitter modules so they self-register."""
    import pais.ingest.splitters  # noqa: F401  (side-effect: register)


_ensure_builtins_loaded()
