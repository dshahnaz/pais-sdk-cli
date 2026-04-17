"""Widget-dispatch tests for `prompt_for_param`.

These pin the invariant the v0.6.7 UX regression broke: every `bool` option
must fire `questionary.confirm` (not `.text`), every `Literal` / Enum /
well-known `str` option must fire `questionary.select`, every `int` option
must fire a validated `.text`. A shell that falls through to free-text for
a `bool` or an enum is the specific break we're preventing here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest
import typer

from pais.cli import _prompts
from pais.cli._introspect import ParamSpec, walk
from pais.cli._prompts import CANCEL, prompt_for_param


class _FakeAsk:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQuestionary:
    """Records which widget was used and returns the scripted answer."""

    def __init__(self, answer: Any = None) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def confirm(self, message: str, **kw: Any) -> _FakeAsk:
        self.calls.append({"kind": "confirm", "message": message, "default": kw.get("default")})
        return _FakeAsk(self.answer)

    def select(self, message: str, *, choices: list[Any], **kw: Any) -> _FakeAsk:
        self.calls.append(
            {"kind": "select", "message": message, "choices": choices, "default": kw.get("default")}
        )
        return _FakeAsk(self.answer)

    def text(self, message: str, **kw: Any) -> _FakeAsk:
        self.calls.append({"kind": "text", "message": message, "default": kw.get("default")})
        return _FakeAsk(self.answer)

    def path(self, message: str, **kw: Any) -> _FakeAsk:
        self.calls.append({"kind": "path", "message": message, "default": kw.get("default")})
        return _FakeAsk(self.answer)


@pytest.fixture
def fq(monkeypatch: pytest.MonkeyPatch) -> _FakeQuestionary:
    fake = _FakeQuestionary()
    monkeypatch.setattr(_prompts, "questionary", fake)
    return fake


# ----- Part A-1: bool → confirm ----------------------------------------------


def test_bool_option_dispatches_to_confirm(fq: _FakeQuestionary) -> None:
    fq.answer = True
    param = ParamSpec(
        name="with_counts",
        annotation=bool,
        kind="option",
        default=False,
        required=False,
        help="toggle me",
    )
    result = prompt_for_param(param)
    assert result is True
    assert fq.calls[-1]["kind"] == "confirm"


def test_bool_with_string_annotation_still_routes_to_confirm(fq: _FakeQuestionary) -> None:
    """Defensive path: if `get_type_hints` couldn't resolve and we got the
    raw PEP 563 string `"bool"`, we should still fire confirm, not text."""
    fq.answer = False
    param = ParamSpec(
        name="epoch", annotation="bool", kind="option", default=False, required=False, help=None
    )
    result = prompt_for_param(param)
    assert result is False
    assert fq.calls[-1]["kind"] == "confirm"


# ----- Part A-2: Literal / Enum / static-enum → select -----------------------


def test_literal_option_dispatches_to_select(fq: _FakeQuestionary) -> None:
    fq.answer = "json"
    param = ParamSpec(
        name="output",
        annotation=Literal["table", "json", "yaml"],
        kind="option",
        default="table",
        required=False,
        help=None,
    )
    result = prompt_for_param(param)
    assert result == "json"
    assert fq.calls[-1]["kind"] == "select"
    assert fq.calls[-1]["choices"] == ["table", "json", "yaml"]


def test_output_str_option_dispatches_to_select_via_static_enum(fq: _FakeQuestionary) -> None:
    """`output` is declared as `str` (not Literal) but lives in
    `_STATIC_ENUMS`. With annotations resolved to a real `str` type, the
    fallback in `_enum_choices` fires."""
    fq.answer = "yaml"
    param = ParamSpec(
        name="output", annotation=str, kind="option", default="table", required=False, help=None
    )
    assert prompt_for_param(param) == "yaml"
    assert fq.calls[-1]["kind"] == "select"
    assert set(fq.calls[-1]["choices"]) == {"table", "json", "yaml"}


def test_strategy_option_dispatches_to_select(fq: _FakeQuestionary) -> None:
    fq.answer = "recreate"
    param = ParamSpec(
        name="strategy", annotation=str, kind="option", default="auto", required=False, help=None
    )
    assert prompt_for_param(param) == "recreate"
    assert fq.calls[-1]["kind"] == "select"
    assert set(fq.calls[-1]["choices"]) == {"auto", "api", "recreate"}


def test_text_splitting_option_dispatches_to_select(fq: _FakeQuestionary) -> None:
    fq.answer = "SEMANTIC"
    param = ParamSpec(
        name="text_splitting",
        annotation=str,
        kind="option",
        default="SENTENCE",
        required=False,
        help=None,
    )
    assert prompt_for_param(param) == "SEMANTIC"
    assert fq.calls[-1]["kind"] == "select"


# ----- Part A-3: int / float → validated text --------------------------------


def test_int_option_dispatches_to_validated_text(fq: _FakeQuestionary) -> None:
    fq.answer = "12"
    param = ParamSpec(
        name="workers", annotation=int, kind="option", default=4, required=False, help=None
    )
    assert prompt_for_param(param) == 12
    assert fq.calls[-1]["kind"] == "text"


def test_float_option_dispatches_to_validated_text(fq: _FakeQuestionary) -> None:
    fq.answer = "0.5"
    param = ParamSpec(
        name="similarity_cutoff",
        annotation=float,
        kind="option",
        default=0.0,
        required=False,
        help=None,
    )
    assert prompt_for_param(param) == 0.5
    assert fq.calls[-1]["kind"] == "text"


def test_int_with_string_annotation_routes_to_text(fq: _FakeQuestionary) -> None:
    """Same PEP 563 defensive path, now for ints."""
    fq.answer = "8"
    param = ParamSpec(
        name="workers", annotation="int", kind="option", default=4, required=False, help=None
    )
    assert prompt_for_param(param) == 8
    assert fq.calls[-1]["kind"] == "text"


# ----- Part A-4: Path → path widget ------------------------------------------


def test_path_option_dispatches_to_path_widget(fq: _FakeQuestionary) -> None:
    fq.answer = "/tmp/some.json"
    param = ParamSpec(
        name="report",
        annotation=Path,
        kind="option",
        default=Path("./r.json"),
        required=False,
        help=None,
    )
    result = prompt_for_param(param)
    assert isinstance(result, Path)
    assert fq.calls[-1]["kind"] == "path"


def test_path_with_string_annotation_dispatches_to_path(fq: _FakeQuestionary) -> None:
    fq.answer = "/tmp/b.json"
    param = ParamSpec(
        name="report", annotation="Path", kind="option", default=None, required=False, help=None
    )
    prompt_for_param(param)
    assert fq.calls[-1]["kind"] == "path"


# ----- Part A-5: cancellation ------------------------------------------------


def test_cancel_on_ask_returning_none(fq: _FakeQuestionary) -> None:
    fq.answer = None
    param = ParamSpec(
        name="with_counts",
        annotation=bool,
        kind="option",
        default=False,
        required=False,
        help=None,
    )
    assert prompt_for_param(param) is CANCEL


# ----- Part A-6: full-tree fall-through audit --------------------------------


def test_every_option_in_live_tree_routes_to_a_typed_widget() -> None:
    """Walk the real `pais` Typer tree. Every param whose annotation resolves
    to `bool`, `int`, `float`, `Path`, a `Literal`, or a known `_STATIC_ENUMS`
    key MUST have a real type object (not a PEP 563 string). Any regression
    that re-introduces string annotations would break widget dispatch.
    """
    from pais.cli.app import app

    specs = walk(app)
    found_bool = found_int = found_static_enum = 0
    for spec in specs:
        for p in spec.params:
            if p.annotation is bool:
                found_bool += 1
            elif p.annotation is int:
                found_int += 1
            elif p.annotation is str and p.name in _prompts._STATIC_ENUMS:
                found_static_enum += 1
            # Every annotation we care about must be a real type (or a
            # Literal/Optional/Union). Never the literal string "bool" etc.
            assert p.annotation != "bool", f"{spec.display}.{p.name} — PEP 563 string leak"
            assert p.annotation != "int", f"{spec.display}.{p.name} — PEP 563 string leak"
            assert p.annotation != "str", f"{spec.display}.{p.name} — PEP 563 string leak"

    assert found_bool > 0, "expected at least one bool option in the live tree"
    assert found_int > 0, "expected at least one int option (workers/chunk_size)"
    assert found_static_enum > 0, "expected at least one --output-like enum option"


_FLAG_OPT = typer.Option(False)


def _sample_bool_callback(flag: bool = _FLAG_OPT) -> None:
    pass


def test_introspect_resolves_future_annotations_in_this_test_file() -> None:
    """This test module itself uses `from __future__ import annotations`.
    Verify that `_params()` sees `flag: bool` as the real `bool` type, not
    the string `"bool"` — i.e. the `_resolve_hints` fix is doing its job."""
    from pais.cli._introspect import _params

    params = _params(_sample_bool_callback)
    (p,) = params
    assert p.name == "flag"
    assert p.annotation is bool
