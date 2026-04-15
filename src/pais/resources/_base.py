"""Generic CRUD + pagination helpers for PAIS resources."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from pais.models.common import ListResponse
from pais.transport.base import Transport

M = TypeVar("M", bound=BaseModel)


class Resource(Generic[M]):
    """Shared CRUD helpers. Subclasses set `path` and `model`."""

    path: str = ""
    model: type[M]

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    # --- low-level helpers ----------------------------------------------------
    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._transport.request("GET", path, params=params).body

    def _post_json(self, path: str, *, json: Any | None = None) -> Any:
        return self._transport.request("POST", path, json=json).body

    def _delete(self, path: str) -> None:
        self._transport.request("DELETE", path)

    # --- generic CRUD ---------------------------------------------------------
    def _list_page(self, **params: Any) -> ListResponse[M]:
        raw = self._get_json(self.path, params=params or None)
        return ListResponse[self.model].model_validate(raw)  # type: ignore[name-defined]

    def list(self, *, limit: int | None = None, after: str | None = None) -> ListResponse[M]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if after is not None:
            params["after"] = after
        return self._list_page(**params)

    def list_all(self, *, limit: int = 100) -> Iterator[M]:
        """Transparently follow `has_more` + `last_id` to iterate every object."""
        after: str | None = None
        while True:
            page = self.list(limit=limit, after=after)
            yield from page.data
            if not page.has_more:
                break
            if page.last_id:
                after = page.last_id
                continue
            if page.data:
                last = page.data[-1]
                after = getattr(last, "id", None)
                if after is None:
                    break
            else:
                break

    def get(self, resource_id: str) -> M:
        raw = self._get_json(f"{self.path}/{resource_id}")
        return self.model.model_validate(raw)

    def _create(self, payload: BaseModel | dict[str, Any]) -> M:
        body = (
            payload.model_dump(mode="json", exclude_none=True)
            if isinstance(payload, BaseModel)
            else payload
        )
        raw = self._post_json(self.path, json=body)
        return self.model.model_validate(raw)

    def _update(self, resource_id: str, payload: BaseModel | dict[str, Any]) -> M:
        body = (
            payload.model_dump(mode="json", exclude_none=True)
            if isinstance(payload, BaseModel)
            else payload
        )
        raw = self._post_json(f"{self.path}/{resource_id}", json=body)
        return self.model.model_validate(raw)

    def delete(self, resource_id: str) -> None:
        self._delete(f"{self.path}/{resource_id}")
