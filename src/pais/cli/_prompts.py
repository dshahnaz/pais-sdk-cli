"""Type-aware prompt builders for the interactive shell.

Each `prompt_for_param` call inspects a `ParamSpec` and asks the user for a
value with the right `questionary` widget — `confirm` for `bool`, `select`
for enums / `Literal`, `path` for `Path`, validated `text` for ints/floats,
plain `text` for strings (unless a context-aware picker overrides — see
`_pickers.py`).

Returned values are typed so the underlying typer callback can be called
directly with `**kwargs` — no string round-tripping.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

import questionary

from pais.cli._introspect import ParamSpec

CANCEL = object()  # sentinel — user aborted (Ctrl-C / Esc)


# Static enum hints for typer-style `str` options whose `help=` enumerates choices.
# Keyed by parameter name; takes effect when the param is a plain `str`.
_STATIC_ENUMS: dict[str, list[str]] = {
    "output": ["table", "json", "yaml"],
    "strategy": ["auto", "api", "recreate"],
    "text_splitting": ["SENTENCE", "SEMANTIC"],
}


def prompt_for_param(param: ParamSpec) -> Any:
    """Ask the user for `param`. Returns the typed value, or `CANCEL` on abort."""
    label = _label(param)
    default = param.default

    # 1. Booleans → confirm.
    if param.annotation is bool:
        return _ok(questionary.confirm(label, default=bool(default)).ask())

    # 2. Literal[...] / Enum → select.
    choices = _enum_choices(param)
    if choices:
        # Coerce Enum-typed defaults to their stringified form so `default in
        # choices` works. Enum.__members__ is keyed by .name; Enum.value may
        # be the same string or a different one — cover both.
        default_key: Any = default
        if default is not None and hasattr(default, "name"):
            default_key = (
                default.name
                if default.name in choices
                else (default.value if getattr(default, "value", None) in choices else None)
            )
        elif isinstance(default, str) and default not in choices:
            default_key = None
        return _ok(
            questionary.select(
                label,
                choices=choices,
                default=default_key if default_key in choices else None,
            ).ask()
        )

    # 3. Path → path widget.
    if _is_path(param.annotation):
        ans = questionary.path(label, default=str(default) if default else "").ask()
        if ans is None:
            return CANCEL
        return Path(ans).expanduser()

    # 4. int / float → text + validator.
    if param.annotation in (int, float):
        cast = int if param.annotation is int else float

        def _validate(v: str, _cast: type = cast) -> bool | str:
            if v.strip() == "":
                if not param.required:
                    return True
                return f"value required ({param.annotation.__name__})"
            try:
                _cast(v)
                return True
            except ValueError:
                return f"must be a valid {_cast.__name__}"

        ans = questionary.text(
            label,
            default=str(default) if default is not None else "",
            validate=_validate,
        ).ask()
        if ans is None:
            return CANCEL
        if ans.strip() == "" and not param.required:
            return default
        return cast(ans)

    # 5. str (and Optional[str]) → plain text (or skip on empty if optional).
    ans = questionary.text(label, default=str(default) if default is not None else "").ask()
    if ans is None:
        return CANCEL
    if ans.strip() == "" and not param.required:
        return default
    return ans


def _label(param: ParamSpec) -> str:
    suffix = " [required]" if param.required else ""
    if param.help:
        return f"{param.name}{suffix} — {param.help}"
    return f"{param.name}{suffix}"


def _enum_choices(param: ParamSpec) -> list[str] | None:
    """Return a fixed choice list if the param is a Literal, an Enum, or a
    well-known str option (output/strategy/text_splitting)."""
    ann = param.annotation
    origin = get_origin(ann)

    # Optional[X] / Union[X, None] — peel.
    if origin in (typing.Union, getattr(typing, "UnionType", type(None))):
        args = [a for a in get_args(ann) if a is not type(None)]
        if len(args) == 1:
            ann = args[0]
            origin = get_origin(ann)

    if origin is Literal:
        return [str(v) for v in get_args(ann)]

    if isinstance(ann, type) and hasattr(ann, "__members__"):
        return [m for m in ann.__members__]

    # Static fallback for str-typed params with documented enums.
    if ann is str and param.name in _STATIC_ENUMS:
        return list(_STATIC_ENUMS[param.name])

    return None


def _is_path(ann: Any) -> bool:
    if ann is Path:
        return True
    origin = get_origin(ann)
    if origin in (typing.Union, getattr(typing, "UnionType", type(None))):
        return any(a is Path for a in get_args(ann))
    return False


def _ok(value: Any) -> Any:
    """`questionary.ask()` returns None when the user hits Ctrl-C."""
    return CANCEL if value is None else value
