"""LRU recent-targets cache: per-profile, MRU first, capped, robust to corruption."""

from __future__ import annotations

from pathlib import Path

import pytest

from pais.cli import _recent


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_recent, "CACHE_PATH", tmp_path / "recent.json")


def test_record_then_recall_orders_mru_first() -> None:
    _recent.record_use("kbs", "first", profile="lab")
    _recent.record_use("kbs", "second", profile="lab")
    _recent.record_use("kbs", "third", profile="lab")
    assert _recent.recent("kbs", profile="lab") == ["third", "second", "first"]


def test_record_dedupes_existing_entries() -> None:
    """Re-using a recent item moves it to the front, no dup."""
    _recent.record_use("kbs", "a", profile="lab")
    _recent.record_use("kbs", "b", profile="lab")
    _recent.record_use("kbs", "a", profile="lab")  # promote
    assert _recent.recent("kbs", profile="lab") == ["a", "b"]


def test_lru_cap_enforced() -> None:
    """11th add evicts the oldest."""
    for i in range(15):
        _recent.record_use("kbs", f"k{i}", profile="lab")
    items = _recent.recent("kbs", profile="lab", limit=20)
    assert len(items) == 10
    # MRU first; the last 10 added should remain (k14..k5).
    assert items[0] == "k14"
    assert items[-1] == "k5"


def test_profiles_isolated() -> None:
    _recent.record_use("kbs", "lab_kb", profile="lab")
    _recent.record_use("kbs", "prod_kb", profile="prod")
    assert _recent.recent("kbs", profile="lab") == ["lab_kb"]
    assert _recent.recent("kbs", profile="prod") == ["prod_kb"]


def test_clear_one_profile_keeps_others() -> None:
    _recent.record_use("kbs", "a", profile="lab")
    _recent.record_use("kbs", "b", profile="prod")
    _recent.clear(profile="lab")
    assert _recent.recent("kbs", profile="lab") == []
    assert _recent.recent("kbs", profile="prod") == ["b"]


def test_clear_all_wipes_file(tmp_path: Path) -> None:
    _recent.record_use("kbs", "x", profile="lab")
    assert _recent.CACHE_PATH.exists()
    _recent.clear()
    assert not _recent.CACHE_PATH.exists()


def test_corrupt_cache_returns_empty(tmp_path: Path) -> None:
    _recent.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _recent.CACHE_PATH.write_text("{not valid json")
    assert _recent.recent("kbs", profile="lab") == []
    # And recording works after corruption (writes a fresh file).
    _recent.record_use("kbs", "fresh", profile="lab")
    assert _recent.recent("kbs", profile="lab") == ["fresh"]


def test_empty_alias_is_a_noop() -> None:
    _recent.record_use("kbs", "", profile="lab")
    assert _recent.recent("kbs", profile="lab") == []
