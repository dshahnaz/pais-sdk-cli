"""BearerAuth — static bearer token from env/config."""

from __future__ import annotations


class BearerAuth:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("BearerAuth requires a non-empty token")
        self._token = token

    def apply(self, headers: dict[str, str]) -> None:
        headers["Authorization"] = f"Bearer {self._token}"

    def refresh(self) -> bool:
        # Static tokens can't refresh themselves.
        return False
