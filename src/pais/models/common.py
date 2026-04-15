"""Shared response envelope + error shapes used across all PAIS resources."""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PaisModel(BaseModel):
    """Base model with tolerant parsing.

    PAIS responses sometimes add fields between releases; we want to accept
    unknown fields (forward-compat) without dropping them, while still
    validating the fields we do know about.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


class ListResponse(PaisModel, Generic[T]):
    object: Literal["list"] = "list"
    data: list[T] = Field(default_factory=list)
    has_more: bool = False
    num_objects: int | None = None
    first_id: str | None = None
    last_id: str | None = None


class ErrorDetailModel(PaisModel):
    error_code: str | None = None
    loc: list[str | int] | None = None
    value: Any = None
    msg: str | None = None


class ErrorResponse(PaisModel):
    detail: list[ErrorDetailModel] | str | None = None
    message: str | None = None
