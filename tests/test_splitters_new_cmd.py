"""`pais splitters new <kind>` scaffolder — generates file + test + __init__ patch + doc row."""

from __future__ import annotations

from pathlib import Path

import pytest

from pais.cli import splitters_new_cmd
from pais.cli.splitters_new_cmd import ScaffoldInput, _render_splitter, _render_test, _update_init


def _scaffold_tree(tmp_path: Path) -> Path:
    (tmp_path / "src" / "pais" / "ingest" / "splitters").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "pais" / "ingest" / "splitters" / "__init__.py").write_text(
        '"""Built-in splitters. Importing this module registers them all."""\n\n'
        "from pais.ingest.splitters import test_suite_arctic, test_suite_bge\n\n"
        '__all__ = ["test_suite_arctic", "test_suite_bge"]\n'
    )
    (tmp_path / "docs" / "ingestion.md").write_text(
        "# Table\n\n| kind | summary |\n|---|---|\n<!-- splitters-table-end -->\n"
    )
    return tmp_path


def _sample_input(kind: str = "widget_split") -> ScaffoldInput:
    return ScaffoldInput(
        kind=kind,
        summary="Chunks Widget XML docs at top-level <widget> boundaries",
        input_type="widget XML files",
        example_input="~/widgets/foo.xml",
        chunk_size_unit="tokens",
        target_embeddings_model="BAAI/bge-small-en-v1.5",
        suggested_index_chunk_size=512,
        suggested_index_chunk_overlap=64,
    )


def test_render_splitter_includes_kind_class_and_meta() -> None:
    src = _render_splitter(_sample_input("widget_split"))
    assert 'kind: ClassVar[str] = "widget_split"' in src
    assert "class WidgetSplitSplitter" in src
    assert "class WidgetSplitOptions" in src
    assert 'target_embeddings_model="BAAI/bge-small-en-v1.5"' in src
    assert "suggested_index_chunk_size=512" in src
    assert "suggested_index_chunk_overlap=64" in src
    assert "@register_splitter" in src
    assert "NotImplementedError" in src  # TODO marker preserved


def test_render_splitter_omits_optional_meta_fields_when_absent() -> None:
    inp = _sample_input()
    inp_no_target = ScaffoldInput(
        kind=inp.kind,
        summary=inp.summary,
        input_type=inp.input_type,
        example_input=inp.example_input,
        chunk_size_unit=inp.chunk_size_unit,
        target_embeddings_model=None,
        suggested_index_chunk_size=None,
        suggested_index_chunk_overlap=None,
    )
    src = _render_splitter(inp_no_target)
    assert "target_embeddings_model" not in src
    assert "suggested_index_chunk_size" not in src


def test_render_test_stub_covers_registration_and_meta() -> None:
    src = _render_test(_sample_input("widget_split"))
    assert 'assert "widget_split" in SPLITTER_REGISTRY' in src
    assert "WidgetSplitSplitter" in src


def test_update_init_inserts_import_alphabetized(tmp_path: Path) -> None:
    root = _scaffold_tree(tmp_path)
    init = root / "src" / "pais" / "ingest" / "splitters" / "__init__.py"
    _update_init(init, "widget_split")
    text = init.read_text()
    assert (
        "from pais.ingest.splitters import test_suite_arctic, test_suite_bge, widget_split" in text
    )
    assert "'widget_split'" in text or '"widget_split"' in text
    # __all__ preserves the full set.
    assert "test_suite_bge" in text
    assert "test_suite_arctic" in text


def test_update_init_is_idempotent(tmp_path: Path) -> None:
    root = _scaffold_tree(tmp_path)
    init = root / "src" / "pais" / "ingest" / "splitters" / "__init__.py"
    _update_init(init, "widget_split")
    before = init.read_text()
    _update_init(init, "widget_split")
    assert init.read_text() == before


def test_scaffold_splitter_dry_run_touches_no_files(tmp_path: Path) -> None:
    root = _scaffold_tree(tmp_path)
    splitters_new_cmd.scaffold_splitter(
        kind="widget_split",
        dry_run=True,
        yes=False,
        repo_root=root,
    )
    # Dry-run wrote nothing.
    assert not (root / "src" / "pais" / "ingest" / "splitters" / "widget_split.py").exists()
    assert not (root / "tests" / "test_splitter_widget_split.py").exists()


def test_scaffold_splitter_real_write_creates_all_artifacts(tmp_path: Path) -> None:
    root = _scaffold_tree(tmp_path)
    splitters_new_cmd.scaffold_splitter(
        kind="widget_split",
        dry_run=False,
        yes=True,
        repo_root=root,
    )
    splitter_path = root / "src" / "pais" / "ingest" / "splitters" / "widget_split.py"
    test_path = root / "tests" / "test_splitter_widget_split.py"
    init_path = root / "src" / "pais" / "ingest" / "splitters" / "__init__.py"
    docs_path = root / "docs" / "ingestion.md"

    assert splitter_path.exists()
    assert test_path.exists()
    assert "widget_split" in init_path.read_text()
    assert "| `widget_split` |" in docs_path.read_text()


def test_scaffold_splitter_rejects_non_snake_case(tmp_path: Path) -> None:
    root = _scaffold_tree(tmp_path)
    import typer

    with pytest.raises(typer.BadParameter, match="snake_case"):
        splitters_new_cmd.scaffold_splitter(
            kind="WidgetSplit",
            dry_run=False,
            yes=True,
            repo_root=root,
        )


def test_scaffold_splitter_refuses_overwrite_without_yes(tmp_path: Path) -> None:
    root = _scaffold_tree(tmp_path)
    target = root / "src" / "pais" / "ingest" / "splitters" / "widget_split.py"
    target.write_text("# existing\n")
    import typer

    with pytest.raises(typer.BadParameter, match="already exists"):
        splitters_new_cmd.scaffold_splitter(
            kind="widget_split",
            dry_run=False,
            yes=False,
            repo_root=root,
        )
