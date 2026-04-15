"""Token-budget tests."""

from __future__ import annotations

from pais.dev.token_budget import BUDGET, token_count


def test_budget_is_400() -> None:
    assert BUDGET == 400


def test_empty_string_is_zero() -> None:
    assert token_count("") == 0


def test_count_is_stable_across_calls() -> None:
    a = token_count("This is a deterministic sentence.")
    b = token_count("This is a deterministic sentence.")
    assert a == b
    assert a > 0


def test_counts_scale_with_length() -> None:
    short = token_count("hello world")
    long = token_count("hello world " * 50)
    assert long > short * 10
