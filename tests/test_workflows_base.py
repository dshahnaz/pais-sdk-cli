"""Tests for workflow primitives: review screen + confirm_by_typing."""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console

from pais.cli._workflows import _base
from pais.cli._workflows._base import (
    BACK,
    FieldSpec,
    ReviewSpec,
    confirm_by_typing,
    prompt_review_screen,
)


class _FakeAsk:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQ:
    def __init__(self) -> None:
        self.scripted: list[Any] = []

    def script(self, *answers: Any) -> None:
        self.scripted = list(answers)

    def text(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))

    def select(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))

    def confirm(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))


@pytest.fixture
def fq(monkeypatch: pytest.MonkeyPatch) -> _FakeQ:
    fq_ = _FakeQ()
    monkeypatch.setattr(_base, "questionary", fq_)
    return fq_


def test_review_screen_go_returns_committed(fq: _FakeQ) -> None:
    fq.script("✅ Go (commit)")
    spec = ReviewSpec(title="t", fields=[FieldSpec(name="x", value=1)])
    out = prompt_review_screen(spec, Console())
    assert out == {"x": 1}


def test_review_screen_back_returns_back_sentinel(fq: _FakeQ) -> None:
    fq.script("← back")
    spec = ReviewSpec(title="t", fields=[FieldSpec(name="x", value=1)])
    assert prompt_review_screen(spec, Console()) is BACK


def test_review_screen_edit_then_go(fq: _FakeQ) -> None:
    """Edit re-prompts the field, then Go commits the new value."""
    fq.script(
        "✏  Edit name",  # pick edit
        "newval",  # text() answer
        "✅ Go (commit)",  # commit
    )
    spec = ReviewSpec(title="t", fields=[FieldSpec(name="name", value="oldval")])
    out = prompt_review_screen(spec, Console())
    assert out == {"name": "newval"}


def test_confirm_by_typing_exact_match_proceeds(fq: _FakeQ) -> None:
    fq.script("kb1")
    assert confirm_by_typing("delete?", expected="kb1") is True


def test_confirm_by_typing_mismatch_cancels(fq: _FakeQ) -> None:
    fq.script("oops")
    assert confirm_by_typing("delete?", expected="kb1") is False


def test_confirm_by_typing_quick_confirm_falls_back_to_yn(
    fq: _FakeQ, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAIS_QUICK_CONFIRM", "1")
    fq.script(True)  # confirm.ask() answers True
    assert confirm_by_typing("delete?", expected="kb1") is True
