"""AuthStrategy protocol. Implementations attach headers to outgoing requests
and refresh credentials on 401."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AuthStrategy(Protocol):
    def apply(self, headers: dict[str, str]) -> None:
        """Mutate `headers` in-place to add auth (or leave unchanged)."""

    def refresh(self) -> bool:
        """Re-acquire credentials. Return True if fresh creds were obtained
        (caller should retry the request), False otherwise."""
