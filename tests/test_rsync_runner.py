"""Tests for skills/sync/scripts/rsync_runner.py.

Argv builder, output parser, and the runner itself (which actually invokes
the system rsync binary).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from config import ConfigError, ProjectConfig, load_wiki_config
from rsync_runner import (
    RsyncError,
    RsyncResult,
    build_rsync_args,
    parse_rsync_output,
    run_rsync,
)

# Skip integration tests if rsync isn't installed (shouldn't happen on CI but be safe).
needs_rsync = pytest.mark.skipif(
    shutil.which("rsync") is None, reason="rsync binary not available"
)


def _make_project(
    *, source: Path, name: str = "demo", excludes: tuple[str, ...] | None = None
) -> ProjectConfig:
    """Build a ProjectConfig directly (skipping load_wiki_config) for unit tests."""
    return ProjectConfig(
        name=name,
        source=source.resolve(),
        raw_subdir=f"raw/{name}",
        wiki_subdir=f"wiki/{name}",
        excludes=excludes if excludes is not None else (".git", ".asof", ".last-sync"),
    )


def _build_pattern_a_wiki(tmp_path: Path) -> tuple[ProjectConfig, Path]:
    """Set up a real Pattern A wiki on disk with one project."""
    wiki_dir = tmp_path / "wiki"
    source = tmp_path / "src"
    source.mkdir()
    cfg_data = {
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
                "excludes": [".git", ".asof", ".last-sync"],
            }
        ],
    }
    wiki_dir.mkdir()
    (wiki_dir / ".asof.json").write_text(json.dumps(cfg_data))
    cfg = load_wiki_config(wiki_dir)
    return cfg.projects[0], wiki_dir


# ─── build_rsync_args ───────────────────────────────────────────────────────


def test_args_include_core_flags(tmp_path: Path) -> None:
    proj = _make_project(source=tmp_path)
    target = tmp_path / "raw"
    args = build_rsync_args(proj, target)
    assert args[0] == "rsync"
    assert "-av" in args
    assert "--delete" in args
    assert "--prune-empty-dirs" in args


def test_args_default_to_safe_links(tmp_path: Path) -> None:
    proj = _make_project(source=tmp_path)
    args = build_rsync_args(proj, tmp_path / "raw")
    assert "--safe-links" in args
    assert "--copy-links" not in args


def test_args_copy_links_when_opted_in(tmp_path: Path) -> None:
    proj = _make_project(source=tmp_path)
    args = build_rsync_args(proj, tmp_path / "raw", follow_symlinks=True)
    assert "--copy-links" in args
    assert "--safe-links" not in args


def test_args_dry_run_flag(tmp_path: Path) -> None:
    proj = _make_project(source=tmp_path)
    args_normal = build_rsync_args(proj, tmp_path / "raw")
    args_dry = build_rsync_args(proj, tmp_path / "raw", dry_run=True)
    assert "--dry-run" not in args_normal
    assert "--dry-run" in args_dry


def test_args_includes_each_user_exclude(tmp_path: Path) -> None:
    proj = _make_project(
        source=tmp_path,
        excludes=("node_modules", ".git", ".asof", ".last-sync", "dist"),
    )
    args = build_rsync_args(proj, tmp_path / "raw")
    assert "--exclude=node_modules" in args
    assert "--exclude=.git" in args
    assert "--exclude=dist" in args


def test_args_include_md_filter_in_correct_order(tmp_path: Path) -> None:
    """The include/exclude order matters: --include=*/ + --include=*.md
    + --exclude=* must appear AFTER user excludes for rsync's first-match
    semantics to skip everything except .md files."""
    proj = _make_project(source=tmp_path)
    args = build_rsync_args(proj, tmp_path / "raw")
    # Find positions
    idx_include_dirs = args.index("--include=*/")
    idx_include_md = args.index("--include=*.md")
    idx_exclude_all = args.index("--exclude=*")
    # Include dirs first, then .md, then exclude everything else
    assert idx_include_dirs < idx_include_md < idx_exclude_all
    # Last two args are source/ and target/
    assert args[-2].endswith("/")  # source has trailing slash
    assert args[-1].endswith("/")  # target has trailing slash


def test_args_source_and_target_have_trailing_slashes(tmp_path: Path) -> None:
    """Trailing slash on source = "copy contents into target", not the dir
    itself. rsync convention."""
    proj = _make_project(source=tmp_path)
    target = tmp_path / "raw"
    args = build_rsync_args(proj, target)
    assert args[-2] == f"{tmp_path.resolve()!s}/"
    assert args[-1] == f"{target!s}/"


def test_args_raises_when_mandatory_excludes_missing(tmp_path: Path) -> None:
    """Defense-in-depth: even if the project somehow lacks .asof in excludes,
    the args builder refuses."""
    bad = _make_project(source=tmp_path, excludes=(".git",))  # missing .asof / .last-sync
    with pytest.raises(ConfigError, match="missing mandatory"):
        build_rsync_args(bad, tmp_path / "raw")


# ─── parse_rsync_output ─────────────────────────────────────────────────────


def test_parse_output_counts_md_transfers() -> None:
    stdout = """\
sending incremental file list
foo.md
docs/bar.md
docs/baz.md

sent 1234 bytes  received 56 bytes
"""
    transferred, deleted = parse_rsync_output(stdout)
    assert transferred == 3
    assert deleted == 0


def test_parse_output_counts_deletions() -> None:
    stdout = """\
sending incremental file list
deleting old.md
deleting docs/stale.md
new.md
"""
    transferred, deleted = parse_rsync_output(stdout)
    assert transferred == 1  # new.md
    assert deleted == 2


def test_parse_output_ignores_non_md_lines() -> None:
    stdout = """\
sending incremental file list
some-binary
script.sh
text.txt
real.md
"""
    transferred, deleted = parse_rsync_output(stdout)
    assert transferred == 1


def test_parse_output_empty_input() -> None:
    assert parse_rsync_output("") == (0, 0)


def test_parse_output_treats_deleting_as_delete_not_transfer() -> None:
    """A 'deleting foo.md' line ends with .md but starts with 'deleting '."""
    stdout = "deleting foo.md\nbar.md\n"
    transferred, deleted = parse_rsync_output(stdout)
    assert transferred == 1  # bar.md only
    assert deleted == 1


# ─── run_rsync (integration with system rsync) ──────────────────────────────


@needs_rsync
def test_run_rsync_happy_path(tmp_path: Path) -> None:
    proj, wiki = _build_pattern_a_wiki(tmp_path)
    # Put two .md files in source
    (proj.source / "a.md").write_text("# a\n")
    (proj.source / "docs").mkdir()
    (proj.source / "docs" / "b.md").write_text("# b\n")
    # And a non-md file (should be filtered out)
    (proj.source / "binary.txt").write_text("not markdown")

    result = run_rsync(proj, wiki)

    assert isinstance(result, RsyncResult)
    assert result.succeeded
    assert result.return_code == 0
    assert result.transferred == 2
    assert result.deleted == 0
    # raw/ now has the two .md files
    raw = proj.raw_path(wiki)
    assert (raw / "a.md").is_file()
    assert (raw / "docs" / "b.md").is_file()
    assert not (raw / "binary.txt").exists()


@needs_rsync
def test_run_rsync_dry_run_does_not_write(tmp_path: Path) -> None:
    proj, wiki = _build_pattern_a_wiki(tmp_path)
    (proj.source / "a.md").write_text("# a\n")
    result = run_rsync(proj, wiki, dry_run=True)
    assert result.succeeded
    assert result.dry_run
    # Nothing should have actually been copied
    raw = proj.raw_path(wiki)
    assert not (raw / "a.md").exists()


@needs_rsync
def test_run_rsync_picks_up_new_file_on_second_run(tmp_path: Path) -> None:
    proj, wiki = _build_pattern_a_wiki(tmp_path)
    (proj.source / "a.md").write_text("# a\n")
    run_rsync(proj, wiki)
    # Add a new file and re-run
    (proj.source / "b.md").write_text("# b\n")
    result = run_rsync(proj, wiki)
    # rsync only transfers what changed
    assert result.transferred == 1


@needs_rsync
def test_run_rsync_deletes_upstream_removed_files(tmp_path: Path) -> None:
    proj, wiki = _build_pattern_a_wiki(tmp_path)
    (proj.source / "a.md").write_text("# a\n")
    (proj.source / "b.md").write_text("# b\n")
    run_rsync(proj, wiki)
    # Remove b.md upstream
    (proj.source / "b.md").unlink()
    result = run_rsync(proj, wiki)
    assert result.deleted == 1
    assert not (proj.raw_path(wiki) / "b.md").exists()


@needs_rsync
def test_run_rsync_excludes_filter_files(tmp_path: Path) -> None:
    proj, wiki = _build_pattern_a_wiki(tmp_path)
    # Create files inside excluded dirs
    (proj.source / ".git").mkdir()
    (proj.source / ".git" / "HEAD.md").write_text("# git head\n")
    (proj.source / "real.md").write_text("# real\n")
    result = run_rsync(proj, wiki)
    assert result.transferred == 1
    assert (proj.raw_path(wiki) / "real.md").is_file()
    assert not (proj.raw_path(wiki) / ".git").exists()


def test_run_rsync_missing_source_raises(tmp_path: Path) -> None:
    """ProjectConfig pointing at a nonexistent dir → RsyncError."""
    proj = _make_project(source=tmp_path / "missing")
    with pytest.raises(RsyncError, match="does not exist"):
        run_rsync(proj, tmp_path / "wiki")


def test_run_rsync_self_ingest_guard_fires(tmp_path: Path) -> None:
    """Pattern-C-style: wiki_dir inside source, .asof missing from excludes."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wiki_dir = repo / ".asof"  # wiki INSIDE source
    wiki_dir.mkdir()
    bad_proj = _make_project(source=repo, excludes=(".git",))  # .asof not excluded!
    with pytest.raises(ConfigError, match="recurse into the wiki itself"):
        run_rsync(bad_proj, wiki_dir)


@needs_rsync
def test_run_rsync_allow_self_bypasses_guard(tmp_path: Path) -> None:
    """`--allow-self` lets power users override the self-ingest check."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / "real.md").write_text("# real\n")
    wiki_dir = repo / ".asof"
    wiki_dir.mkdir()
    bad_proj = _make_project(source=repo, excludes=(".git", ".asof", ".last-sync"))
    # With .asof properly excluded the guard wouldn't fire anyway, but verify
    # the override works without error.
    result = run_rsync(bad_proj, wiki_dir, allow_self=True)
    assert result.succeeded


# ─── RsyncError.result attachment ──────────────────────────────────────────


def test_rsync_error_carries_result_when_available() -> None:
    fake_result = RsyncResult(
        project_name="x",
        return_code=23,
        transferred=0,
        deleted=0,
        dry_run=False,
        raw_stdout="",
        raw_stderr="permission denied",
    )
    err = RsyncError("bad", result=fake_result)
    assert err.result is fake_result
    assert "bad" in str(err)
