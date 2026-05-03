"""Tests for skills/sync/scripts/delta.py.

Frontmatter parsing, source-summary indexing (with the rglob fix), delta
detection, symlink policy, strict-mtime mode.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from config import ProjectConfig, load_wiki_config
from delta import (
    DeletedSummary,
    DeltaReport,
    ModifiedRaw,
    NewRaw,
    SkippedSymlink,
    StrictMtimeError,
    build_source_index,
    detect_deltas,
    extract_frontmatter,
    parse_sources,
)

# ─── extract_frontmatter ────────────────────────────────────────────────────


def test_extract_frontmatter_valid() -> None:
    text = """---
title: Source — foo.md
type: source-summary
sources:
  - path: raw/x/foo.md
    source_mtime: 2026-04-26
---

body here
"""
    fm = extract_frontmatter(text)
    assert fm is not None
    assert "title: Source — foo.md" in fm
    assert "sources:" in fm


def test_extract_frontmatter_no_fence() -> None:
    assert extract_frontmatter("# Just a heading\n") is None


def test_extract_frontmatter_no_closing_fence() -> None:
    text = "---\ntitle: x\n# never closes"
    assert extract_frontmatter(text) is None


def test_extract_frontmatter_must_start_at_first_line() -> None:
    """Per common YAML-frontmatter convention, the fence must be at line 1."""
    text = "\n---\ntitle: x\n---\n"
    assert extract_frontmatter(text) is None


# ─── parse_sources ──────────────────────────────────────────────────────────


def test_parse_sources_single_entry() -> None:
    fm = """sources:
  - path: raw/x/foo.md
    source_mtime: 2026-04-26
    ingested: 2026-04-26
"""
    assert parse_sources(fm) == [("raw/x/foo.md", "2026-04-26")]


def test_parse_sources_multiple_entries() -> None:
    fm = """sources:
  - path: raw/x/foo.md
    source_mtime: 2026-04-26
    ingested: 2026-04-26
  - path: raw/x/bar.md
    source_mtime: 2026-03-15
"""
    result = parse_sources(fm)
    assert len(result) == 2
    assert ("raw/x/foo.md", "2026-04-26") in result
    assert ("raw/x/bar.md", "2026-03-15") in result


def test_parse_sources_skips_entries_missing_mtime() -> None:
    fm = """sources:
  - path: raw/x/foo.md
    ingested: 2026-04-26
  - path: raw/x/bar.md
    source_mtime: 2026-03-15
"""
    # First entry has no source_mtime → skipped
    assert parse_sources(fm) == [("raw/x/bar.md", "2026-03-15")]


def test_parse_sources_no_sources_block() -> None:
    fm = "title: x\ntags: [foo]"
    assert parse_sources(fm) == []


def test_parse_sources_handles_4space_indent() -> None:
    """Some users use 4-space indent under `sources:`. Must still parse."""
    fm = """sources:
    - path: raw/x/foo.md
      source_mtime: 2026-04-26
"""
    assert parse_sources(fm) == [("raw/x/foo.md", "2026-04-26")]


# ─── build_source_index ─────────────────────────────────────────────────────


def _write_summary(
    sources_dir: Path, rel: str, raw_path: str, mtime: str
) -> None:
    """Write a source-summary at sources_dir/rel with one cited source."""
    target = sources_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"""---
title: Source — {rel}
type: source-summary
sources:
  - path: {raw_path}
    source_mtime: {mtime}
    ingested: 2026-04-26
last_updated: 2026-04-26
---

body
"""
    )


def test_build_source_index_recursive_rglob_fix(tmp_path: Path) -> None:
    """The rglob fix: nested source-summaries MUST be indexed.

    Codifies the bug discovered during traddea brain-sync work — `glob("*.md")`
    silently missed `sources/docs/foo.md` and produced 162 false-positive
    NEW deltas. The recursive walk is what makes the wiki layout work.
    """
    sources = tmp_path / "wiki" / "sources"
    _write_summary(sources, "top.md", "raw/proj/top.md", "2026-01-01")
    _write_summary(sources, "docs/nested.md", "raw/proj/docs/nested.md", "2026-02-01")
    _write_summary(
        sources, "docs/archive/old.md", "raw/proj/docs/archive/old.md", "2026-03-01"
    )

    idx = build_source_index(sources, "raw/proj")

    assert "raw/proj/top.md" in idx
    assert "raw/proj/docs/nested.md" in idx  # the bug case
    assert "raw/proj/docs/archive/old.md" in idx  # also nested


def test_build_source_index_filters_by_raw_subdir(tmp_path: Path) -> None:
    """Cross-project: foo's summaries don't pollute bar's index."""
    sources = tmp_path / "wiki" / "sources"
    _write_summary(sources, "foo.md", "raw/foo/x.md", "2026-01-01")
    _write_summary(sources, "bar.md", "raw/bar/y.md", "2026-01-01")

    idx_foo = build_source_index(sources, "raw/foo")
    idx_bar = build_source_index(sources, "raw/bar")

    assert "raw/foo/x.md" in idx_foo
    assert "raw/bar/y.md" not in idx_foo
    assert "raw/bar/y.md" in idx_bar
    assert "raw/foo/x.md" not in idx_bar


def test_build_source_index_missing_dir_returns_empty(tmp_path: Path) -> None:
    idx = build_source_index(tmp_path / "does-not-exist", "raw/x")
    assert idx == {}


def test_build_source_index_skips_files_without_frontmatter(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "no-fm.md").write_text("# Just a markdown file\n")
    _write_summary(sources, "ok.md", "raw/x/foo.md", "2026-01-01")
    idx = build_source_index(sources, "raw/x")
    assert "raw/x/foo.md" in idx
    assert len(idx) == 1


# ─── detect_deltas ──────────────────────────────────────────────────────────


def _build_project_wiki(tmp_path: Path) -> tuple[ProjectConfig, Path]:
    """Build a Pattern A wiki with one project for delta tests."""
    wiki_dir = tmp_path / "wiki"
    source = tmp_path / "src"
    source.mkdir()
    config_data = {
        "wiki_dir": str(wiki_dir),
        "schema_version": "1.0",
        "min_reader_version": "1.0",
        "min_writer_version": "1.0",
        "projects": [
            {
                "name": "demo",
                "source": str(source),
                "raw_subdir": "raw/demo",
                "wiki_subdir": "wiki/demo",
                "excludes": [".asof", ".last-sync"],
            }
        ],
    }
    wiki_dir.mkdir()
    (wiki_dir / ".asof.json").write_text(json.dumps(config_data))
    cfg = load_wiki_config(wiki_dir)
    return cfg.projects[0], wiki_dir


def _put_raw(wiki_dir: Path, project: ProjectConfig, rel: str, mtime_year: int) -> Path:
    """Create a raw .md file with a deterministic mtime."""
    import os

    target = project.raw_path(wiki_dir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# {rel}\n")
    ts = _timestamp(mtime_year)
    os.utime(target, (ts, ts))
    return target


def _timestamp(year: int) -> float:
    import datetime as dt

    return dt.datetime(year, 1, 1, 12, 0, 0).timestamp()


def _put_summary(
    wiki_dir: Path, project: ProjectConfig, rel: str, raw_path: str, mtime: str
) -> Path:
    sources = project.wiki_path(wiki_dir) / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    target = sources / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"""---
title: Source — {rel}
type: source-summary
sources:
  - path: {raw_path}
    source_mtime: {mtime}
    ingested: 2026-04-26
last_updated: 2026-04-26
---
body
"""
    )
    return target


def test_detect_deltas_empty_wiki_empty_raw(tmp_path: Path) -> None:
    proj, wiki = _build_project_wiki(tmp_path)
    report = detect_deltas(proj, wiki)
    assert isinstance(report, DeltaReport)
    assert report.is_empty
    assert report.total_changes == 0


def test_detect_deltas_all_new(tmp_path: Path) -> None:
    proj, wiki = _build_project_wiki(tmp_path)
    _put_raw(wiki, proj, "a.md", 2026)
    _put_raw(wiki, proj, "docs/b.md", 2026)
    report = detect_deltas(proj, wiki)
    assert len(report.new) == 2
    assert {n.rel_path for n in report.new} == {"a.md", "docs/b.md"}
    assert all(n.mtime == "2026-01-01" for n in report.new)
    assert len(report.modified) == 0
    assert len(report.deleted) == 0


def test_detect_deltas_modified(tmp_path: Path) -> None:
    proj, wiki = _build_project_wiki(tmp_path)
    _put_raw(wiki, proj, "a.md", 2026)  # current mtime: 2026-01-01
    _put_summary(wiki, proj, "a.md", "raw/demo/a.md", "2025-01-01")  # recorded older
    report = detect_deltas(proj, wiki)
    assert len(report.modified) == 1
    m = report.modified[0]
    assert m.rel_path == "a.md"
    assert m.old_mtime == "2025-01-01"
    assert m.new_mtime == "2026-01-01"
    assert len(report.new) == 0


def test_detect_deltas_deleted(tmp_path: Path) -> None:
    proj, wiki = _build_project_wiki(tmp_path)
    # Summary exists but raw file does not
    _put_summary(wiki, proj, "ghost.md", "raw/demo/ghost.md", "2026-01-01")
    report = detect_deltas(proj, wiki)
    assert len(report.deleted) == 1
    d = report.deleted[0]
    assert d.raw_path == "raw/demo/ghost.md"
    assert "ghost.md" in d.summary_path


def test_detect_deltas_mixed(tmp_path: Path) -> None:
    proj, wiki = _build_project_wiki(tmp_path)
    # NEW: raw exists, no summary
    _put_raw(wiki, proj, "new.md", 2026)
    # MODIFIED: raw + summary, mtimes differ
    _put_raw(wiki, proj, "modified.md", 2026)
    _put_summary(
        wiki, proj, "modified.md", "raw/demo/modified.md", "2024-06-01"
    )
    # DELETED: summary exists, raw doesn't
    _put_summary(
        wiki, proj, "deleted.md", "raw/demo/deleted.md", "2024-01-01"
    )
    # UNCHANGED: matching mtimes (must NOT appear)
    _put_raw(wiki, proj, "unchanged.md", 2026)
    _put_summary(
        wiki, proj, "unchanged.md", "raw/demo/unchanged.md", "2026-01-01"
    )

    report = detect_deltas(proj, wiki)
    assert {n.rel_path for n in report.new} == {"new.md"}
    assert {m.rel_path for m in report.modified} == {"modified.md"}
    assert {d.raw_path for d in report.deleted} == {"raw/demo/deleted.md"}
    assert report.total_changes == 3


def test_detect_deltas_skips_symlinks_by_default(tmp_path: Path) -> None:
    proj, wiki = _build_project_wiki(tmp_path)
    _put_raw(wiki, proj, "real.md", 2026)
    raw_dir = proj.raw_path(wiki)
    link = raw_dir / "linked.md"
    link.symlink_to(raw_dir / "real.md")
    report = detect_deltas(proj, wiki)
    # Real file → NEW; symlink → SkippedSymlink
    assert {n.rel_path for n in report.new} == {"real.md"}
    assert len(report.skipped_symlinks) == 1
    assert report.skipped_symlinks[0].rel_path == "linked.md"


def test_detect_deltas_follows_symlinks_when_opted_in(tmp_path: Path) -> None:
    proj, wiki = _build_project_wiki(tmp_path)
    _put_raw(wiki, proj, "real.md", 2026)
    raw_dir = proj.raw_path(wiki)
    link = raw_dir / "linked.md"
    link.symlink_to(raw_dir / "real.md")
    report = detect_deltas(proj, wiki, follow_symlinks=True)
    # Both real and linked appear as NEW; no skipped entries
    assert {n.rel_path for n in report.new} == {"real.md", "linked.md"}
    assert len(report.skipped_symlinks) == 0


def test_detect_deltas_strict_mtime_raises_on_regression(tmp_path: Path) -> None:
    """If the recorded mtime is NEWER than the current file, that's a bug."""
    proj, wiki = _build_project_wiki(tmp_path)
    _put_raw(wiki, proj, "x.md", 2024)  # current: 2024-01-01
    _put_summary(wiki, proj, "x.md", "raw/demo/x.md", "2026-01-01")  # recorded NEWER
    with pytest.raises(StrictMtimeError, match="regression"):
        detect_deltas(proj, wiki, strict_mtime=True)


def test_detect_deltas_strict_mtime_off_treats_regression_as_modified(
    tmp_path: Path,
) -> None:
    """Default behavior: regression is reported as MODIFIED (old > new)."""
    proj, wiki = _build_project_wiki(tmp_path)
    _put_raw(wiki, proj, "x.md", 2024)
    _put_summary(wiki, proj, "x.md", "raw/demo/x.md", "2026-01-01")
    report = detect_deltas(proj, wiki, strict_mtime=False)
    assert len(report.modified) == 1
    assert report.modified[0].old_mtime == "2026-01-01"
    assert report.modified[0].new_mtime == "2024-01-01"


def test_detect_deltas_unchanged_files_are_not_reported(tmp_path: Path) -> None:
    proj, wiki = _build_project_wiki(tmp_path)
    _put_raw(wiki, proj, "stable.md", 2026)
    _put_summary(wiki, proj, "stable.md", "raw/demo/stable.md", "2026-01-01")
    report = detect_deltas(proj, wiki)
    assert report.is_empty


# ─── DeltaReport properties ─────────────────────────────────────────────────


def test_delta_report_total_changes_excludes_skipped() -> None:
    """`total_changes` counts NEW+MODIFIED+DELETED only (skipped symlinks
    are informational, not changes the agent must ingest)."""
    rep = DeltaReport(
        project_name="x",
        raw_subdir="raw/x",
        wiki_subdir="wiki/x",
        new=(NewRaw("a", "2026-01-01"),),
        modified=(ModifiedRaw("b", "2025-01-01", "2026-01-01"),),
        deleted=(DeletedSummary("raw/x/c", "/wiki/c.md"),),
        skipped_symlinks=(SkippedSymlink("d", "/elsewhere"),),
    )
    assert rep.total_changes == 3
    assert not rep.is_empty
