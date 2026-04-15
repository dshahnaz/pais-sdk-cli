"""NoAuth — the default for internal-network PAIS deployments."""

from __future__ import annotations


class NoAuth:
    def apply(self, headers: dict[str, str]) -> None:
        return None

    def refresh(self) -> bool:
        return False
