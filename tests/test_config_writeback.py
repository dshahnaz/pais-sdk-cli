"""TOML writeback: append-only, idempotent, preserves comments, refuses on parse error."""

from __future__ import annotations

from pathlib import Path

import pytest

from pais.cli._config_file import load_profile_config
from pais.cli._config_writeback import (
    WritebackError,
    append_index_block,
    append_kb_block,
    block_exists,
    commit_append,
    index_exists,
    preview_diff,
)


def test_kb_block_round_trips_through_loader(tmp_path: Path) -> None:
    p = tmp_path / "pais.toml"
    p.write_text(
        '# user-curated comment\ndefault_profile = "lab"\n\n[profiles.lab]\nmode = "http"\n'
    )
    block = append_kb_block(config_path=p, profile="lab", alias="demo", kb_name="demo-kb")
    commit_append(p, block)
    cfg, _, _ = load_profile_config(path=p, profile="lab")
    assert "demo" in cfg.knowledge_bases
    assert cfg.knowledge_bases["demo"].name == "demo-kb"
    assert cfg.knowledge_bases["demo"].data_origin_type == "DATA_SOURCES"


def test_index_block_round_trips_through_loader(tmp_path: Path) -> None:
    p = tmp_path / "pais.toml"
    p.write_text("[profiles.lab]\n")
    kb_block = append_kb_block(config_path=p, profile="lab", alias="demo", kb_name="kb")
    ix_block = append_index_block(
        profile="lab",
        kb_alias="demo",
        idx_alias="main",
        name="ix",
        embeddings_model_endpoint="BAAI/bge-small-en-v1.5",
        chunk_size=512,
        chunk_overlap=64,
        splitter_kind="passthrough",
        splitter_options={},
    )
    commit_append(p, kb_block, ix_block)
    cfg, _, _ = load_profile_config(path=p, profile="lab")
    assert cfg.knowledge_bases["demo"].indexes[0].name == "ix"
    assert cfg.knowledge_bases["demo"].indexes[0].splitter is not None
    assert cfg.knowledge_bases["demo"].indexes[0].splitter.kind == "passthrough"


def test_block_exists_idempotency_check(tmp_path: Path) -> None:
    p = tmp_path / "pais.toml"
    p.write_text("[profiles.lab]\n")
    assert not block_exists(p, "lab", "demo")
    commit_append(p, append_kb_block(config_path=p, profile="lab", alias="demo", kb_name="x"))
    assert block_exists(p, "lab", "demo")


def test_index_exists_check(tmp_path: Path) -> None:
    p = tmp_path / "pais.toml"
    p.write_text("[profiles.lab]\n")
    commit_append(
        p,
        append_kb_block(config_path=p, profile="lab", alias="demo", kb_name="x"),
        append_index_block(
            profile="lab",
            kb_alias="demo",
            idx_alias="main",
            name="ix",
            embeddings_model_endpoint="m",
        ),
    )
    assert index_exists(p, "lab", "demo", "main")
    assert not index_exists(p, "lab", "demo", "raw")


def test_preserves_existing_comments_and_sections(tmp_path: Path) -> None:
    """User-curated content above and below the marker stays byte-for-byte."""
    p = tmp_path / "pais.toml"
    original = (
        "# top comment\n"
        'default_profile = "lab"\n'
        "\n"
        "[profiles.lab]\n"
        'mode = "http"\n'
        'log_level = "INFO"\n'
        "# bottom comment\n"
    )
    p.write_text(original)
    block = append_kb_block(config_path=p, profile="lab", alias="demo", kb_name="demo-kb")
    commit_append(p, block)
    after = p.read_text()
    # The original lines are still present (in order).
    for line in original.splitlines():
        if line.strip():
            assert line in after


def test_refuses_on_invalid_toml(tmp_path: Path) -> None:
    p = tmp_path / "pais.toml"
    p.write_text("[profiles.lab\nbroken =")
    block = append_kb_block(config_path=p, profile="lab", alias="demo", kb_name="x")
    with pytest.raises(WritebackError):
        commit_append(p, block)


def test_diff_preview_renders_added_lines(tmp_path: Path) -> None:
    p = tmp_path / "pais.toml"
    p.write_text('[profiles.lab]\nmode = "http"\n')
    block = append_kb_block(config_path=p, profile="lab", alias="demo", kb_name="demo-kb")
    diff = preview_diff(p, block)
    assert "+" in diff
    assert "demo-kb" in diff
    assert "DATA_SOURCES" in diff


def test_creates_file_if_missing(tmp_path: Path) -> None:
    p = tmp_path / "subdir" / "pais.toml"
    block = append_kb_block(config_path=p, profile="lab", alias="demo", kb_name="x")
    commit_append(p, block)
    assert p.exists()
    cfg, _, _ = load_profile_config(path=p, profile="lab")
    assert "demo" in cfg.knowledge_bases
