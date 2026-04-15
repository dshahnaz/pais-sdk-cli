"""CLI output helpers — table/json/yaml selection + exit code mapping."""

from __future__ import annotations

import json as _json
import sys
from typing import Any

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from pais.errors import (
    PaisAuthError,
    PaisError,
    PaisValidationError,
)

EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_API_ERROR = 2
EXIT_AUTH_ERROR = 3


def exit_code_for(exc: Exception) -> int:
    if isinstance(exc, PaisAuthError):
        return EXIT_AUTH_ERROR
    if isinstance(exc, PaisValidationError):
        return EXIT_USER_ERROR
    if isinstance(exc, PaisError):
        return EXIT_API_ERROR
    return EXIT_USER_ERROR


def _to_native(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json", exclude_none=True)
    if isinstance(obj, list):
        return [_to_native(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    return obj


def render(obj: Any, *, fmt: str = "table", columns: list[str] | None = None) -> None:
    fmt = (fmt or "table").lower()
    native = _to_native(obj)
    if fmt == "json":
        sys.stdout.write(_json.dumps(native, indent=2, default=str) + "\n")
        return
    if fmt == "yaml":
        import yaml

        sys.stdout.write(yaml.safe_dump(native, sort_keys=False))
        return
    # table
    _render_table(native, columns=columns)


def _render_table(obj: Any, *, columns: list[str] | None) -> None:
    console = Console()
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        cols = columns or list(obj[0].keys())
        table = Table()
        for c in cols:
            table.add_column(c)
        for row in obj:
            table.add_row(*(str(row.get(c, "")) for c in cols))
        console.print(table)
        return
    if isinstance(obj, dict):
        table = Table("field", "value")
        for k, v in obj.items():
            table.add_row(k, str(v))
        console.print(table)
        return
    console.print(str(obj))
