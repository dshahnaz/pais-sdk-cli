"""Walk a Typer app's command tree into flat `CommandSpec` records.

The interactive shell uses these to build the menu — adding a new typer
command anywhere in the tree makes it appear in the menu automatically,
no parallel hand-maintained list to keep in sync.

Each `CommandSpec` carries the underlying callback so the dispatcher can
call it directly with collected kwargs (no subprocess, no re-entering
typer's argv parser).
"""

from __future__ import annotations

import inspect
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import typer
from typer.models import ArgumentInfo, OptionInfo


@dataclass
class ParamSpec:
    """One parameter on a leaf command's callback."""

    name: str
    annotation: Any
    kind: str  # "argument" | "option"
    default: Any  # the actual default value (Ellipsis for required Arguments)
    required: bool
    help: str | None
    param_decls: tuple[str, ...] | None = None  # ("--epoch", "-e"); None for arguments
    hidden: bool = False  # typer.Option(..., hidden=True) → skipped in the shell


@dataclass
class CommandSpec:
    """One executable leaf in the menu tree."""

    path: tuple[str, ...]  # e.g. ("kb", "show") or ("status",) or ("ingest",)
    callback: Callable[..., Any]
    help: str | None
    params: list[ParamSpec] = field(default_factory=list)

    @property
    def display(self) -> str:
        return " ".join(self.path)


def walk(app: typer.Typer) -> list[CommandSpec]:
    """Return every executable command in the app, depth-first, in declared order.

    Includes leaf `app.command(...)` entries on the root, every leaf inside
    every `add_typer(...)` group, and any group's `invoke_without_command=True`
    callback (which acts as a leaf at the group's path — `ingest` does this).
    """
    out: list[CommandSpec] = []
    _walk(app, path=(), out=out)
    return out


def _walk(node: typer.Typer, *, path: tuple[str, ...], out: list[CommandSpec]) -> None:
    for cmd in node.registered_commands:
        cb = cmd.callback
        if cb is None:
            continue
        out.append(
            CommandSpec(
                path=(*path, cmd.name or cb.__name__),
                callback=cb,
                help=_first_line(cmd.help or cb.__doc__),
                params=_params(cb),
            )
        )

    for group in node.registered_groups:
        sub = group.typer_instance
        if sub is None:
            continue
        sub_path = (*path, group.name or "")
        # If the group's callback takes parameters AND invoke_without_command is on,
        # the group itself acts as a leaf (the `ingest` pattern).
        cb_info = sub.registered_callback
        if cb_info is not None and cb_info.callback is not None:
            cb = cb_info.callback
            params = _params(cb)
            invoke_solo = cb_info.invoke_without_command or sub.info.invoke_without_command or False
            if params and invoke_solo:
                out.append(
                    CommandSpec(
                        path=sub_path,
                        callback=cb,
                        help=_first_line(cb_info.help or sub.info.help or cb.__doc__),
                        params=params,
                    )
                )
        _walk(sub, path=sub_path, out=out)


def _resolve_hints(cb: Callable[..., Any]) -> dict[str, Any]:
    """Return {param_name: resolved-type} using PEP 563 resolution.

    Under `from __future__ import annotations`, `inspect.signature(cb)`
    returns annotations as strings (e.g. `"bool"`), which breaks downstream
    `is bool` / `in (int, float)` type checks in `_prompts.py`. Running
    `typing.get_type_hints` resolves those strings back to real type objects.
    Falls back to `{}` on any resolution failure (forward refs, missing
    imports) — callers default to the raw `inspect.Parameter.annotation`.
    """
    try:
        return typing.get_type_hints(cb, include_extras=True)
    except Exception:
        return {}


def _params(cb: Callable[..., Any]) -> list[ParamSpec]:
    hints = _resolve_hints(cb)
    out: list[ParamSpec] = []
    for name, p in inspect.signature(cb).parameters.items():
        ann = hints.get(name, p.annotation)
        info = p.default
        if isinstance(info, ArgumentInfo):
            required = info.default is Ellipsis or info.default is ...
            out.append(
                ParamSpec(
                    name=name,
                    annotation=ann,
                    kind="argument",
                    default=None if required else info.default,
                    required=required,
                    help=info.help,
                    param_decls=None,
                )
            )
        elif isinstance(info, OptionInfo):
            required = info.default is Ellipsis or info.default is ...
            out.append(
                ParamSpec(
                    name=name,
                    annotation=ann,
                    kind="option",
                    default=None if required else info.default,
                    required=required,
                    help=info.help,
                    param_decls=tuple(info.param_decls) if info.param_decls else None,
                    hidden=bool(getattr(info, "hidden", False)),
                )
            )
        else:
            # Plain Python default with no typer wrapper — treat as optional option.
            out.append(
                ParamSpec(
                    name=name,
                    annotation=ann,
                    kind="option",
                    default=info if info is not inspect.Parameter.empty else None,
                    required=info is inspect.Parameter.empty,
                    help=None,
                    param_decls=None,
                )
            )
    return out


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.strip().splitlines():
        if line.strip():
            return line.strip()
    return None
