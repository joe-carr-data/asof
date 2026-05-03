"""Integration tests for skills/sync/scripts/sync.py.

End-to-end: build a synthetic wiki + source on disk, invoke main() with
argv, assert exit codes / stdout / last-sync side-effects.

Tests deliberately use the real `rsync` binary (skipped if unavailable) so
the integration is real, not mocked. Each test runs in its own tmp dir so
parallelism is safe.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from sync import ExitCode, main

needs_rsync = pytest.mark.skipif(
    shutil.which("rsync") is None, reason="rsync binary not available"
)


# ─── fixtures ───────────────────────────────────────────────────────────────


def _make_wiki(
    tmp_path: Path,
    *,
    n_projects: int = 1,
    pattern_c: bool = False,
    schema: str = "1.0",
    min_reader: str = "0.0.0",  # any asof version can read by default
    min_writer: str = "0.0.0",  # any asof version can write by default
) -> tuple[Path, list[Path]]:
    """Create a Pattern A or Pattern C wiki with N projects + source dirs."""
    sources: list[Path] = []
    if pattern_c:
        # Pattern C: wiki dir is <repo>/.asof
        repo = tmp_path / "repo"
        repo.mkdir()
        wiki_dir = repo / ".asof"
        wiki_dir.mkdir()
        cfg: dict[str, Any] = {
            "schema_version": schema,
            "min_reader_version": min_reader,
            "min_writer_version": min_writer,
            "projects": [
                {
                    "name": "myrepo",
                    "raw_subdir": "raw/myrepo",
                    "wiki_subdir": "wiki/myrepo",
                    "excludes": [".asof", ".last-sync"],
                }
            ],
        }
        sources.append(repo)
    else:
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        cfg = {
            "wiki_dir": str(wiki_dir),
            "schema_version": schema,
            "min_reader_version": min_reader,
            "min_writer_version": min_writer,
            "projects": [],
        }
        for i in range(n_projects):
            src = tmp_path / f"src-{i}"
            src.mkdir()
            sources.append(src)
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
    return wiki_dir, sources


# ─── CLI smoke ──────────────────────────────────────────────────────────────


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "asof:sync" in capsys.readouterr().out


def test_help_flag_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "Sync source repo" in capsys.readouterr().out


# ─── error paths (no rsync needed) ──────────────────────────────────────────


def test_missing_wiki_dir_returns_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(["--wiki-dir", str(tmp_path / "does-not-exist"), "--all"])
    assert rc == ExitCode.CONFIG_ERROR
    assert "no asof config" in capsys.readouterr().err


def test_invalid_config_returns_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / ".asof.json").write_text("not json")
    rc = main(["--wiki-dir", str(wiki), "--all"])
    assert rc == ExitCode.CONFIG_ERROR
    assert "invalid JSON" in capsys.readouterr().err


def test_unknown_project_returns_selection_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki, _ = _make_wiki(tmp_path, n_projects=1)
    rc = main(["--wiki-dir", str(wiki), "--project", "nonexistent"])
    assert rc == ExitCode.PROJECT_SELECTION_ERROR
    assert "no project named" in capsys.readouterr().err


def test_no_project_arg_no_match_in_non_interactive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """No --project, cwd outside any source, --non-interactive → fail-fast."""
    wiki, _ = _make_wiki(tmp_path, n_projects=2)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rc = main(["--wiki-dir", str(wiki), "--non-interactive"])
    assert rc == ExitCode.PROJECT_SELECTION_ERROR
    assert "not inside any configured" in capsys.readouterr().err


def test_compat_refuse_skill_too_old(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiki requires asof >= 9.9.9; we run as 0.1.0-dev → REFUSE."""
    wiki, _ = _make_wiki(tmp_path, n_projects=1, min_reader="9.9.9", min_writer="9.9.9")
    rc = main(["--wiki-dir", str(wiki), "--all"])
    assert rc == ExitCode.COMPAT_REFUSED
    assert "Upgrade asof" in capsys.readouterr().err


def test_compat_read_only_blocks_sync(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """min_reader ≤ skill < min_writer → READ_ONLY → sync rejected."""
    wiki, _ = _make_wiki(
        tmp_path, n_projects=1, min_reader="0.1.0", min_writer="9.9.9"
    )
    rc = main(["--wiki-dir", str(wiki), "--all"])
    assert rc == ExitCode.COMPAT_REFUSED
    assert "read-only mode" in capsys.readouterr().err


def test_compat_require_migrate_without_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wiki schema = 0.5; skill schema = 1.0 → REQUIRE_MIGRATE."""
    wiki, _ = _make_wiki(
        tmp_path, n_projects=1, schema="0.5", min_reader="0.1", min_writer="0.1"
    )
    rc = main(["--wiki-dir", str(wiki), "--all"])
    assert rc == ExitCode.COMPAT_REFUSED
    assert "--migrate" in capsys.readouterr().err


def test_migrate_flag_currently_refuses_with_clear_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """v1: --migrate is documented but not implemented; refuses cleanly."""
    wiki, _ = _make_wiki(tmp_path, n_projects=1)
    rc = main(["--wiki-dir", str(wiki), "--all", "--migrate"])
    assert rc == ExitCode.CONFIG_ERROR
    assert "not yet implemented" in capsys.readouterr().err


# ─── successful syncs (need rsync) ──────────────────────────────────────────


@needs_rsync
def test_sync_single_project_happy_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki, sources = _make_wiki(tmp_path, n_projects=1)
    src = sources[0]
    (src / "a.md").write_text("# a\n")
    (src / "docs").mkdir()
    (src / "docs" / "b.md").write_text("# b\n")

    rc = main(["--wiki-dir", str(wiki), "--project", "proj-0"])

    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "=== project: proj-0 ===" in out
    assert "NEW (2)" in out
    assert "=== summary ===" in out
    # Last-sync file written
    assert (wiki / ".last-sync" / "proj-0.json").is_file()


@needs_rsync
def test_sync_all_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wiki, sources = _make_wiki(tmp_path, n_projects=2)
    (sources[0] / "x.md").write_text("# x\n")
    (sources[1] / "y.md").write_text("# y\n")

    rc = main(["--wiki-dir", str(wiki), "--all"])
    assert rc == ExitCode.SUCCESS

    # Both per-project last-sync files written
    last_sync = wiki / ".last-sync"
    assert (last_sync / "proj-0.json").is_file()
    assert (last_sync / "proj-1.json").is_file()


@needs_rsync
def test_sync_dry_run_skips_last_sync_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki, sources = _make_wiki(tmp_path, n_projects=1)
    (sources[0] / "a.md").write_text("# a\n")

    rc = main(["--wiki-dir", str(wiki), "--all", "--dry-run"])
    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    # No files actually copied; no last-sync file
    assert not (wiki / "raw" / "proj-0" / "a.md").exists()
    assert not (wiki / ".last-sync").exists()
    assert "(dry-run)" in out


@needs_rsync
def test_sync_dry_run_skips_delta_detection_with_clear_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex round-1 phase-1 M3: dry-run was misleading because deltas were
    computed against (unchanged) raw/ even though source had changed.

    Fix: skip delta detection in dry-run; print explicit note.
    """
    wiki, sources = _make_wiki(tmp_path, n_projects=1)
    (sources[0] / "a.md").write_text("# a\n")
    rc = main(["--wiki-dir", str(wiki), "--all", "--dry-run"])
    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    # The clear note must appear
    assert "[dry-run] delta detection skipped" in out
    # No NEW/MODIFIED/DELETED counts (since we didn't compute them)
    assert "NEW (" not in out
    assert "MODIFIED (" not in out
    assert "DELETED (" not in out
    # Run summary explicitly notes deltas skipped
    assert "deltas:          (skipped in dry-run mode)" in out


def test_sync_invalid_version_in_config_returns_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codex round-1 phase-1 M2: a malformed schema_version must NOT crash
    with a stack trace; it must surface as a clean ConfigError + exit 2."""
    wiki, _ = _make_wiki(tmp_path, n_projects=1, schema="not-a-version")
    rc = main(["--wiki-dir", str(wiki), "--all"])
    assert rc == ExitCode.CONFIG_ERROR
    err = capsys.readouterr().err
    assert "not a valid version string" in err


@needs_rsync
def test_sync_interactive_all_at_prompt_picks_all_projects(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex round-1 phase-1 M1: previous _prompt_project_choice returned
    projects[0] when user picked "a" (all) — silent data loss because
    other projects never synced. Fix: returns "ALL" sentinel and main()
    re-resolves with all_projects=True.
    """
    wiki, sources = _make_wiki(tmp_path, n_projects=2)
    (sources[0] / "x.md").write_text("# x\n")
    (sources[1] / "y.md").write_text("# y\n")
    # Make cwd match BOTH projects via nesting trick: put cwd into a path
    # that is inside src-0 AND src-0/sub-1 (where sub-1 is a sibling
    # symlink to src-1). Simpler: just put both sources under a common
    # parent and chdir there — but resolve_projects uses _is_inside which
    # checks descendency. Let me create the nesting properly.
    nested = sources[0] / "nested"
    nested.mkdir()
    # Force a multi-match: rewrite config so source[1] is a child of source[0]
    cfg_path = wiki / ".asof.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["projects"][1]["source"] = str(nested)
    cfg_path.write_text(json.dumps(cfg))
    monkeypatch.chdir(nested)
    # Mock input() to pick "a"
    monkeypatch.setattr("builtins.input", lambda _prompt="": "a")

    rc = main(["--wiki-dir", str(wiki)])  # interactive, no flags

    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    # BOTH projects must have been synced — not just projects[0].
    assert "=== project: proj-0 ===" in out
    assert "=== project: proj-1 ===" in out


@needs_rsync
def test_sync_cwd_auto_select(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cwd inside proj-1's source → auto-select proj-1."""
    wiki, sources = _make_wiki(tmp_path, n_projects=2)
    (sources[1] / "x.md").write_text("# x\n")
    monkeypatch.chdir(sources[1])

    rc = main(["--wiki-dir", str(wiki)])  # no --project, no --all

    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "=== project: proj-1 ===" in out
    assert "=== project: proj-0 ===" not in out


@needs_rsync
def test_sync_no_changes_says_up_to_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki, sources = _make_wiki(tmp_path, n_projects=1)
    # Empty source dir — no .md files

    rc = main(["--wiki-dir", str(wiki), "--all"])
    assert rc == ExitCode.SUCCESS
    assert "wiki is up to date." in capsys.readouterr().out


@needs_rsync
def test_sync_summary_only_suppresses_listings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki, sources = _make_wiki(tmp_path, n_projects=1)
    for i in range(5):
        (sources[0] / f"f{i}.md").write_text(f"# f{i}\n")

    rc = main(["--wiki-dir", str(wiki), "--all", "--summary-only"])
    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "NEW (5):" in out
    # Per-file lines must not appear
    assert "f0.md" not in out


@needs_rsync
def test_sync_pattern_c(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end Pattern C: wiki inside repo, source auto-derived."""
    wiki, sources = _make_wiki(tmp_path, pattern_c=True)
    repo = sources[0]
    (repo / "README.md").write_text("# repo\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("# design\n")
    # cd into repo so walk-up resolution finds the wiki
    monkeypatch.chdir(repo)

    rc = main([])  # no args — walk-up + cwd auto-select

    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "=== project: myrepo ===" in out
    # 2 .md files transferred (README.md and docs/design.md). The .asof
    # subdir must NOT have been copied into raw (self-ingest guard).
    raw = wiki / "raw" / "myrepo"
    assert (raw / "README.md").is_file()
    assert (raw / "docs" / "design.md").is_file()
    assert not (raw / ".asof").exists()


# ─── concurrency ────────────────────────────────────────────────────────────


@needs_rsync
def test_lock_serializes_concurrent_runs(tmp_path: Path) -> None:
    """Two simultaneous syncs against the same wiki must not corrupt state.

    We can't easily test true parallelism here without threading, but we can
    verify that the lock file is created/used (proxy for the contract).
    """
    wiki, sources = _make_wiki(tmp_path, n_projects=1)
    (sources[0] / "a.md").write_text("# a\n")

    rc = main(["--wiki-dir", str(wiki), "--all"])
    assert rc == ExitCode.SUCCESS
    # Lock file persists after run (kernel flock auto-releases; file stays as
    # an empty marker — that's normal)
    assert (wiki / ".asof.lock").exists()


# ─── env honor ──────────────────────────────────────────────────────────────


@needs_rsync
def test_asof_dir_env_resolves_wiki(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wiki, sources = _make_wiki(tmp_path, n_projects=1)
    (sources[0] / "a.md").write_text("# a\n")
    monkeypatch.setenv("ASOF_DIR", str(wiki))

    rc = main(["--all"])
    assert rc == ExitCode.SUCCESS


def test_asof_non_interactive_env_respected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ASOF_NON_INTERACTIVE=1 acts like --non-interactive."""
    wiki, _ = _make_wiki(tmp_path, n_projects=2)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("ASOF_NON_INTERACTIVE", "1")
    rc = main(["--wiki-dir", str(wiki)])  # no --non-interactive flag
    # Without env: cwd would prompt; with env: fails-fast
    assert rc == ExitCode.PROJECT_SELECTION_ERROR
