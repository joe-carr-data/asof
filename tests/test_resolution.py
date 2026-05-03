"""Tests for skills/sync/scripts/resolution.py.

Wiki-dir resolution chain, project auto-select (cwd-aware), version compat
matrix, and self-ingest guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from config import (
    DEFAULT_WIKI_DIR,
    ConfigError,
    ProjectConfig,
    WikiConfig,
    load_wiki_config,
)
from resolution import (
    CompatStatus,
    ProjectSelectionError,
    check_self_ingest_safe,
    check_version_compat,
    resolve_projects,
    resolve_wiki_dir,
)

# ─── fixtures ───────────────────────────────────────────────────────────────


def _write_pattern_a(
    wiki_dir: Path,
    source: Path,
    *,
    name: str = "demo",
    schema: str = "1.0",
    min_reader: str = "1.0",
    min_writer: str = "1.0",
) -> None:
    wiki_dir.mkdir(parents=True, exist_ok=True)
    cfg: dict[str, Any] = {
        "wiki_dir": str(wiki_dir),
        "schema_version": schema,
        "min_reader_version": min_reader,
        "min_writer_version": min_writer,
        "projects": [
            {
                "name": name,
                "source": str(source),
                "raw_subdir": f"raw/{name}",
                "wiki_subdir": f"wiki/{name}",
                "excludes": [".git", ".asof", ".last-sync"],
            }
        ],
    }
    (wiki_dir / ".asof.json").write_text(json.dumps(cfg))


def _build_wiki_with_n_projects(
    tmp_path: Path,
    n: int,
    *,
    nested: bool = False,
) -> WikiConfig:
    """Build a WikiConfig with N project entries.

    If nested=True, project[1].source = project[0].source / "subdir" so the
    inner project is nested inside the outer (used to test multi-match).
    """
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    cfg: dict[str, Any] = {
        "wiki_dir": str(wiki_dir),
        "schema_version": "1.0",
        "min_reader_version": "1.0",
        "min_writer_version": "1.0",
        "projects": [],
    }
    for i in range(n):
        src = (
            tmp_path / f"src-{i}"
            if i == 0 or not nested
            else tmp_path / "src-0" / f"sub-{i}"
        )
        src.mkdir(parents=True, exist_ok=True)
        cfg["projects"].append(
            {
                "name": f"proj-{i}",
                "source": str(src),
                "raw_subdir": f"raw/proj-{i}",
                "wiki_subdir": f"wiki/proj-{i}",
                "excludes": [".asof", ".last-sync"],
            }
        )
    (wiki_dir / ".asof.json").write_text(json.dumps(cfg))
    return load_wiki_config(wiki_dir)


# ─── resolve_wiki_dir ───────────────────────────────────────────────────────


def test_resolve_wiki_dir_arg_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit-wiki"
    result = resolve_wiki_dir(explicit, env={"ASOF_DIR": "/should/be/ignored"})
    assert result == explicit.resolve()


def test_resolve_wiki_dir_env_wins_over_walkup_and_default(tmp_path: Path) -> None:
    env_dir = tmp_path / "env-wiki"
    result = resolve_wiki_dir(env={"ASOF_DIR": str(env_dir)}, cwd=tmp_path)
    assert result == env_dir.resolve()


def test_resolve_wiki_dir_walkup_pattern_c(tmp_path: Path) -> None:
    """When cwd is inside a repo with .asof/.asof.json, walk-up finds it."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    asof_dir = repo / ".asof"
    asof_dir.mkdir()
    (asof_dir / ".asof.json").write_text("{}")
    deep = repo / "src" / "module"
    deep.mkdir(parents=True)

    result = resolve_wiki_dir(env={}, cwd=deep)
    assert result == asof_dir.resolve()


def test_resolve_wiki_dir_walkup_bare_config(tmp_path: Path) -> None:
    """When cwd is inside a dir containing bare .asof.json, walk-up finds it."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / ".asof.json").write_text("{}")
    deep = wiki_dir / "subdir"
    deep.mkdir()

    result = resolve_wiki_dir(env={}, cwd=deep)
    assert result == wiki_dir.resolve()


def test_resolve_wiki_dir_falls_back_to_default(tmp_path: Path) -> None:
    """No arg, no env, no walk-up match → default path."""
    bare = tmp_path / "no-config-here"
    bare.mkdir()
    result = resolve_wiki_dir(env={}, cwd=bare)
    assert result == DEFAULT_WIKI_DIR.resolve()


def test_resolve_wiki_dir_arg_with_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`~` expands to home in the arg path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = resolve_wiki_dir("~/some-wiki", env={})
    assert result == (tmp_path / "some-wiki").resolve()


# ─── resolve_projects ───────────────────────────────────────────────────────


def test_resolve_projects_all(tmp_path: Path) -> None:
    cfg = _build_wiki_with_n_projects(tmp_path, n=3)
    result = resolve_projects(cfg, all_projects=True)
    assert len(result) == 3
    assert {p.name for p in result} == {"proj-0", "proj-1", "proj-2"}


def test_resolve_projects_by_name(tmp_path: Path) -> None:
    cfg = _build_wiki_with_n_projects(tmp_path, n=3)
    result = resolve_projects(cfg, name="proj-1")
    assert len(result) == 1
    assert result[0].name == "proj-1"


def test_resolve_projects_by_name_slugifies(tmp_path: Path) -> None:
    """Lookup uses the slugified form so users can pass display names."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=2)
    result = resolve_projects(cfg, name="Proj 1")  # → "proj-1"
    assert result[0].name == "proj-1"


def test_resolve_projects_unknown_name_raises(tmp_path: Path) -> None:
    cfg = _build_wiki_with_n_projects(tmp_path, n=2)
    with pytest.raises(ProjectSelectionError, match="no project named 'unknown'"):
        resolve_projects(cfg, name="unknown")


def test_resolve_projects_cwd_single_match(tmp_path: Path) -> None:
    cfg = _build_wiki_with_n_projects(tmp_path, n=3)
    proj0_source = cfg.projects[0].source
    result = resolve_projects(cfg, cwd=proj0_source)
    assert len(result) == 1
    assert result[0].name == "proj-0"


def test_resolve_projects_cwd_descendant_of_source(tmp_path: Path) -> None:
    """cwd doesn't have to be `source` exactly — any descendant works."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=2)
    deep = cfg.projects[1].source / "subdir" / "deep"
    deep.mkdir(parents=True)
    result = resolve_projects(cfg, cwd=deep)
    assert result[0].name == "proj-1"


def test_resolve_projects_cwd_no_match_raises(tmp_path: Path) -> None:
    cfg = _build_wiki_with_n_projects(tmp_path, n=2)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    with pytest.raises(ProjectSelectionError, match="not inside any configured"):
        resolve_projects(cfg, cwd=elsewhere)


def test_resolve_projects_no_projects_configured(tmp_path: Path) -> None:
    """No-projects wiki gives a friendlier error message."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=0)
    with pytest.raises(ProjectSelectionError, match="no projects configured"):
        resolve_projects(cfg, cwd=tmp_path)


def test_resolve_projects_all_on_empty_wiki_raises(tmp_path: Path) -> None:
    """Codex round-1 phase-1 L1: --all on a wiki with zero projects must
    raise (previously returned empty list silently → 0-project sync that
    looked successful)."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=0)
    with pytest.raises(ProjectSelectionError, match="no projects are configured"):
        resolve_projects(cfg, all_projects=True)


def test_resolve_projects_multi_match_interactive_returns_all(tmp_path: Path) -> None:
    """Nested sources: interactive mode returns all matches for the caller to prompt."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=2, nested=True)
    inner = cfg.projects[1].source
    # cwd inside inner = matches both projects (proj-1 contains it, proj-0 contains it via nesting)
    result = resolve_projects(cfg, cwd=inner, non_interactive=False)
    assert len(result) == 2


def test_resolve_projects_multi_match_non_interactive_raises(tmp_path: Path) -> None:
    cfg = _build_wiki_with_n_projects(tmp_path, n=2, nested=True)
    inner = cfg.projects[1].source
    with pytest.raises(ProjectSelectionError, match="matches multiple projects"):
        resolve_projects(cfg, cwd=inner, non_interactive=True)


def test_resolve_projects_multi_match_auto_select_longest(tmp_path: Path) -> None:
    """`--auto-select-longest` picks the deepest source, deterministically."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=2, nested=True)
    inner = cfg.projects[1].source
    result = resolve_projects(
        cfg, cwd=inner, non_interactive=True, auto_select_longest=True
    )
    assert len(result) == 1
    # Inner has the longer path → it wins
    assert result[0].name == "proj-1"


# ─── check_version_compat (the four-cell matrix) ────────────────────────────


def test_compat_cell_a_skill_too_old(tmp_path: Path) -> None:
    """skill < min_reader → REFUSE."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=1)
    result = check_version_compat(cfg, skill_version="0.5.0")  # < min_reader 1.0.0
    assert result.status == CompatStatus.REFUSE
    assert "Upgrade asof" in result.message


def test_compat_cell_b_read_only(tmp_path: Path) -> None:
    """min_reader ≤ skill < min_writer → READ_ONLY."""
    wiki_dir = tmp_path / "wiki"
    source = tmp_path / "source"
    source.mkdir()
    _write_pattern_a(wiki_dir, source, min_reader="1.0", min_writer="2.0")
    cfg = load_wiki_config(wiki_dir)
    result = check_version_compat(cfg, skill_version="1.5.0")
    assert result.status == CompatStatus.READ_ONLY
    assert "cannot write to it" in result.message


def test_compat_cell_c_require_migrate(tmp_path: Path) -> None:
    """skill ≥ min_writer AND wiki_schema < skill_schema → REQUIRE_MIGRATE."""
    wiki_dir = tmp_path / "wiki"
    source = tmp_path / "source"
    source.mkdir()
    _write_pattern_a(wiki_dir, source, schema="0.9", min_reader="1.0", min_writer="1.0")
    cfg = load_wiki_config(wiki_dir)
    result = check_version_compat(
        cfg, skill_version="1.0.0", skill_schema_version="1.0"
    )
    assert result.status == CompatStatus.REQUIRE_MIGRATE
    assert "--migrate" in result.message


def test_compat_cell_d_allowed(tmp_path: Path) -> None:
    """skill ≥ min_writer AND wiki_schema ≥ skill_schema → ALLOWED."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=1)  # all 1.0
    result = check_version_compat(cfg, skill_version="1.0.0")
    assert result.status == CompatStatus.ALLOWED


def test_compat_cell_d_newer_skill_older_wiki_works(tmp_path: Path) -> None:
    """Newer skill on older wiki schema must NOT trigger migration if already current."""
    wiki_dir = tmp_path / "wiki"
    source = tmp_path / "source"
    source.mkdir()
    # skill_schema = 1.0, wiki_schema = 1.0 → equal → cell (d) ALLOWED
    _write_pattern_a(wiki_dir, source, schema="1.0", min_reader="0.5", min_writer="0.5")
    cfg = load_wiki_config(wiki_dir)
    result = check_version_compat(
        cfg, skill_version="2.0.0", skill_schema_version="1.0"
    )
    assert result.status == CompatStatus.ALLOWED


def test_compat_skill_exactly_at_min_reader_is_read_only(tmp_path: Path) -> None:
    """Boundary: skill == min_reader < min_writer → READ_ONLY (cell b)."""
    wiki_dir = tmp_path / "wiki"
    source = tmp_path / "source"
    source.mkdir()
    _write_pattern_a(wiki_dir, source, min_reader="1.0", min_writer="2.0")
    cfg = load_wiki_config(wiki_dir)
    result = check_version_compat(cfg, skill_version="1.0.0")
    assert result.status == CompatStatus.READ_ONLY


def test_compat_skill_exactly_at_min_writer_is_allowed(tmp_path: Path) -> None:
    """Boundary: skill == min_writer → cell (c) or (d), not (b)."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=1)  # min_writer = 1.0
    result = check_version_compat(cfg, skill_version="1.0.0")
    assert result.status == CompatStatus.ALLOWED


# ─── check_self_ingest_safe ─────────────────────────────────────────────────


def test_self_ingest_pattern_a_passes(tmp_path: Path) -> None:
    """Pattern A: wiki_dir is OUTSIDE source. No risk of recursion."""
    cfg = _build_wiki_with_n_projects(tmp_path, n=1)
    # wiki_dir is tmp_path/"wiki", source is tmp_path/"src-0" — disjoint.
    check_self_ingest_safe(cfg.projects[0], cfg.wiki_dir)  # must not raise


def test_self_ingest_pattern_c_with_excludes_passes(tmp_path: Path) -> None:
    """Pattern C: wiki_dir INSIDE source, but .asof IS excluded. Safe."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wiki_dir = repo / ".asof"
    wiki_dir.mkdir()
    cfg_data: dict[str, Any] = {
        "schema_version": "1.0",
        "min_reader_version": "1.0",
        "min_writer_version": "1.0",
        "projects": [
            {
                "name": "myrepo",
                "raw_subdir": "raw/myrepo",
                "wiki_subdir": "wiki/myrepo",
                "excludes": [".asof", ".last-sync"],
            }
        ],
    }
    (wiki_dir / ".asof.json").write_text(json.dumps(cfg_data))
    cfg = load_wiki_config(wiki_dir)
    # Must not raise: .asof is excluded
    check_self_ingest_safe(cfg.projects[0], cfg.wiki_dir)


def test_self_ingest_defense_in_depth_raises_when_excludes_tampered(
    tmp_path: Path,
) -> None:
    """If a Pattern-C ProjectConfig is mutated to drop .asof, runtime guard fires.

    Loading via load_wiki_config would have rejected it (mandatory excludes),
    but check_self_ingest_safe is called as a belt-and-suspenders runtime
    check. We verify it actually catches the case.
    """
    repo = tmp_path / "myrepo"
    repo.mkdir()
    # Build a project manually that bypasses load_wiki_config's checks.
    bad_project = ProjectConfig(
        name="myrepo",
        source=repo,
        raw_subdir="raw/myrepo",
        wiki_subdir="wiki/myrepo",
        excludes=(".git",),  # NOTE: .asof missing
    )
    wiki_dir = repo / ".asof"
    wiki_dir.mkdir()
    with pytest.raises(ConfigError, match="recurse into the wiki itself"):
        check_self_ingest_safe(bad_project, wiki_dir)
