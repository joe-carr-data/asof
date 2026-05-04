"""init → sync → lint chain: bootstrap, ingest sources, audit.

Phase 5 cross-phase integration. Verifies the canonical user flow:
  1. /asof:init bootstraps the wiki.
  2. User drops *.md files into the source dir.
  3. /asof:sync mirrors them into raw/ and reports deltas.
  4. /asof:lint audits the resulting state.

Each step runs as a real subprocess. Asserts on exit codes, mtime
preservation, and that synced files don't trigger lint's path-mismatch
or frontmatter checks.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from .conftest import run_skill


def _write_source_file(source_dir: Path, rel: str, content: str) -> Path:
    target = source_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


# ─── Pattern A end-to-end ─────────────────────────────────────────────────


def test_pattern_a_init_sync_lint_with_sample_md(
    pattern_a_wiki: Path, tmp_path: Path
) -> None:
    """Drop a couple of *.md files in source, sync them, lint must stay clean."""
    source = tmp_path / "src"  # created by pattern_a_wiki fixture
    _write_source_file(source, "intro.md", "# Intro\n\nProject overview.\n")
    _write_source_file(source, "design/notes.md", "# Notes\n")

    sync_result = run_skill(
        "sync",
        [
            "myproj",
            "--wiki-dir", str(pattern_a_wiki),
            "--non-interactive",
            "--summary-only",
        ],
    )
    assert sync_result.returncode == 0, sync_result.stderr

    # raw/ now contains the two files, mirroring the source tree.
    raw_root = pattern_a_wiki / "raw" / "myproj"
    assert (raw_root / "intro.md").is_file()
    assert (raw_root / "design" / "notes.md").is_file()

    # Lint still clean — sync doesn't create any wiki/<project>/ pages
    # automatically, so there are no source-summaries to validate.
    lint_result = run_skill("lint", ["--wiki-dir", str(pattern_a_wiki)])
    assert lint_result.returncode == 0, lint_result.stderr
    assert "Wiki is clean" in lint_result.stdout


def test_sync_preserves_source_mtime_in_raw(
    pattern_a_wiki: Path, tmp_path: Path
) -> None:
    """sync uses rsync with --times so raw/ mtimes match source/ mtimes
    (within rsync's second-precision rounding). Lint's mtime-drift check
    relies on this — if sync clobbered mtimes, every page would drift."""
    source = tmp_path / "src"
    src_file = _write_source_file(source, "x.md", "# X\n")
    target_mtime = time.time() - 60 * 60 * 24 * 7  # one week ago
    os.utime(src_file, (target_mtime, target_mtime))

    run_skill(
        "sync",
        [
            "myproj",
            "--wiki-dir", str(pattern_a_wiki),
            "--non-interactive",
            "--summary-only",
        ],
    ).assert_success("sync after mtime adjust")

    raw_file = pattern_a_wiki / "raw" / "myproj" / "x.md"
    assert raw_file.is_file()
    # rsync rounds to seconds; allow 2s tolerance for cross-FS quirks.
    assert abs(raw_file.stat().st_mtime - target_mtime) < 2.0


# ─── source-summary validates with synced raw file ─────────────────────────


def test_source_summary_pointing_to_synced_raw_passes_lint(
    pattern_a_wiki: Path, tmp_path: Path
) -> None:
    """A user-authored source-summary that references an actual synced raw
    file must pass lint's path-mismatch + frontmatter checks end-to-end."""
    source = tmp_path / "src"
    _write_source_file(source, "topic.md", "# Topic\n\nReal content.\n")

    # Sync first — populates raw/myproj/topic.md.
    run_skill(
        "sync",
        [
            "myproj",
            "--wiki-dir", str(pattern_a_wiki),
            "--non-interactive",
            "--summary-only",
        ],
    ).assert_success("sync")

    # Now write a source-summary pointing at the synced file.
    project_dir = pattern_a_wiki / "wiki" / "myproj" / "sources"
    project_dir.mkdir(parents=True, exist_ok=True)
    raw_mtime = time.strftime(
        "%Y-%m-%d",
        time.gmtime((pattern_a_wiki / "raw" / "myproj" / "topic.md").stat().st_mtime),
    )
    (project_dir / "topic.md").write_text(
        "---\n"
        "title: Topic summary\n"
        "type: source-summary\n"
        "project: myproj\n"
        f"last_updated: {raw_mtime}\n"
        "sources:\n"
        "  - path: raw/myproj/topic.md\n"
        f"    source_mtime: {raw_mtime}\n"
        f"    ingested: {raw_mtime}\n"
        "---\n\nA summary of the synced topic.\n",
        encoding="utf-8",
    )
    # Link from index.md so it isn't flagged as orphan.
    index = pattern_a_wiki / "wiki" / "myproj" / "index.md"
    index_text = index.read_text(encoding="utf-8")
    index.write_text(
        index_text.replace(
            "## Source summaries",
            "## Source summaries\n- [Topic summary](sources/topic.md)\n",
        ),
        encoding="utf-8",
    )

    lint_result = run_skill(
        "lint", ["--wiki-dir", str(pattern_a_wiki)]
    )
    assert lint_result.returncode == 0, lint_result.stdout + lint_result.stderr


# ─── source-summary with a missing raw → lint catches it ───────────────────


def test_source_summary_with_missing_raw_fails_lint(
    pattern_a_wiki: Path, tmp_path: Path
) -> None:
    """If a source-summary points at a raw path that doesn't exist (e.g.
    the source was deleted but the summary wasn't marked removed_upstream),
    lint's path-mismatch ERROR must fire."""
    project_dir = pattern_a_wiki / "wiki" / "myproj" / "sources"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "ghost.md").write_text(
        "---\n"
        "title: Ghost\n"
        "type: source-summary\n"
        "project: myproj\n"
        "last_updated: 2026-05-04\n"
        "sources:\n"
        "  - path: raw/myproj/never-existed.md\n"
        "    source_mtime: 2026-05-04\n"
        "    ingested: 2026-05-04\n"
        "---\nbody\n",
        encoding="utf-8",
    )
    lint_result = run_skill(
        "lint", ["--wiki-dir", str(pattern_a_wiki), "--severity", "error"]
    )
    assert lint_result.returncode == 1, lint_result.stdout
    assert "path-mismatch" in lint_result.stdout
    assert "never-existed.md" in lint_result.stdout


# ─── Pattern C end-to-end ─────────────────────────────────────────────────


def test_pattern_c_init_sync_lint(pattern_c_wiki: Path) -> None:
    """Pattern C: bootstrap + drop *.md inside the repo + sync + lint."""
    repo = pattern_c_wiki.parent
    _write_source_file(repo, "README.md", "# Repo README\n")
    _write_source_file(repo, "docs/intro.md", "# Intro\n")

    # In Pattern C, sync auto-selects the single project from cwd.
    sync_result = run_skill(
        "sync",
        ["--non-interactive", "--summary-only"],
        cwd=repo,
    )
    assert sync_result.returncode == 0, sync_result.stderr

    # Files mirrored into <repo>/.asof/raw/myrepo/.
    assert (pattern_c_wiki / "raw" / "myrepo" / "README.md").is_file()
    assert (pattern_c_wiki / "raw" / "myrepo" / "docs" / "intro.md").is_file()

    # Lint clean.
    lint_result = run_skill("lint", cwd=repo)
    assert lint_result.returncode == 0, lint_result.stderr
    assert "Wiki is clean" in lint_result.stdout
