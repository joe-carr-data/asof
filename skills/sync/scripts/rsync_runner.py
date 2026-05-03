"""rsync invocation wrapper for asof:sync.

Builds the rsync command with the right flags, runs it, captures + parses
stdout/stderr, returns structured `RsyncResult`. All safety guards
(self-ingest, mandatory excludes, dry-run) gate the invocation.

Why a wrapper module:
    - rsync flag set is opinionated (`-av --delete --prune-empty-dirs`,
      include-only `*.md`, mandatory excludes for safety). Centralizing
      avoids drift if multiple call sites grow later.
    - Symlink policy switch (`--safe-links` default vs `--copy-links`
      opt-in) lives in one place — not scattered across CLI and helpers.
    - Counting transferred / deleted files from rsync stdout is brittle
      against version drift; isolate the parser so a single fix updates
      every consumer.

Stdlib only (uses the system `rsync` binary).
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

from config import MANDATORY_EXCLUDES, ConfigError, ProjectConfig
from resolution import check_self_ingest_safe

# ─── data model ─────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class RsyncResult:
    """Outcome of one rsync invocation for a single project."""

    project_name: str
    return_code: int
    transferred: int  # number of .md files copied
    deleted: int  # number of files removed by --delete
    dry_run: bool
    raw_stdout: str  # for debugging / final report
    raw_stderr: str

    @property
    def succeeded(self) -> bool:
        return self.return_code == 0


class RsyncError(RuntimeError):
    """Raised when rsync returns non-zero or the runner pre-checks fail.

    The wrapped result (if any) is attached so callers can surface the
    raw stderr to the user.
    """

    def __init__(self, message: str, result: RsyncResult | None = None) -> None:
        super().__init__(message)
        self.result = result


# ─── command builder ────────────────────────────────────────────────────────


def build_rsync_args(
    project: ProjectConfig,
    raw_target: Path,
    *,
    follow_symlinks: bool = False,
    dry_run: bool = False,
) -> list[str]:
    """Build the argv list for the rsync invocation.

    Layout (the order matters for include/exclude pattern resolution):
        rsync -av --delete --prune-empty-dirs
              <symlink-flag>                           # --safe-links | --copy-links
              [--dry-run]
              --exclude=<each>                         # user excludes (per .asof.json)
              --include=*/                             # descend into all dirs
              --include=*.md                           # but only mirror .md files
              --exclude=*                              # exclude everything else
              <source>/                                # trailing slash matters
              <raw_target>/

    Trailing slash on source = "copy contents into target" (vs creating
    a `<source>` subdir under target). Same convention as the original
    brain-sync.

    Pre-conditions enforced at config-load time (load_wiki_config) but
    re-checked here belt-and-suspenders style:
    - mandatory excludes (.asof, .last-sync) MUST be in the list.
    """
    missing = MANDATORY_EXCLUDES - set(project.excludes)
    if missing:
        raise ConfigError(
            f"refusing to build rsync args: project {project.name!r} is "
            f"missing mandatory excludes {sorted(missing)}. This would "
            f"risk Pattern C self-ingest. Restore the excludes via init."
        )

    args: list[str] = ["rsync", "-av", "--delete", "--prune-empty-dirs"]
    args.append("--copy-links" if follow_symlinks else "--safe-links")
    if dry_run:
        args.append("--dry-run")
    for exc in project.excludes:
        args.append(f"--exclude={exc}")
    args.extend(["--include=*/", "--include=*.md", "--exclude=*"])
    args.append(f"{project.source!s}/")
    args.append(f"{raw_target!s}/")
    return args


# ─── runner ─────────────────────────────────────────────────────────────────


def run_rsync(
    project: ProjectConfig,
    wiki_dir: Path,
    *,
    follow_symlinks: bool = False,
    dry_run: bool = False,
    allow_self: bool = False,
) -> RsyncResult:
    """Run rsync for one project and return a structured result.

    Performs three pre-checks before invoking rsync:
        1. The system `rsync` binary exists on PATH.
        2. The project's source directory exists (rsync would fail anyway
           but we want a clearer error).
        3. The self-ingest guard (PLAN C1): refuse if `wiki_dir` is inside
           `source` and `.asof` is missing from excludes. Pass
           `allow_self=True` to override (rare, --allow-self CLI flag).

    Args:
        project: which project to sync.
        wiki_dir: root of the wiki (used to derive raw_target).
        follow_symlinks: if True, pass `--copy-links` (resolve symlinks
            as files); otherwise `--safe-links` (skip ones pointing
            outside the source).
        dry_run: if True, pass `--dry-run`; rsync prints what it would do
            without touching the filesystem.
        allow_self: bypass the self-ingest guard. Use only when you know
            what you're doing (Pattern C with .asof properly excluded).

    Returns:
        `RsyncResult` with counts and raw output.

    Raises:
        RsyncError: if the binary is missing, the source doesn't exist,
            the self-ingest guard fires, or rsync exits non-zero.
        ConfigError: if mandatory excludes are missing (re-checked here).
    """
    if shutil.which("rsync") is None:
        raise RsyncError(
            "the `rsync` binary is required but not found on PATH. "
            "Install it: `brew install rsync` (macOS) or your package "
            "manager (Linux)."
        )
    if not project.source.exists():
        raise RsyncError(
            f"project {project.name!r} source {project.source!s} does "
            f"not exist. Edit `.asof.json` or recreate the source dir."
        )
    if not allow_self:
        # ConfigError raised here flows up unchanged — caller prints it.
        check_self_ingest_safe(project, wiki_dir)

    raw_target = project.raw_path(wiki_dir)
    raw_target.mkdir(parents=True, exist_ok=True)

    argv = build_rsync_args(
        project,
        raw_target,
        follow_symlinks=follow_symlinks,
        dry_run=dry_run,
    )
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)

    transferred, deleted = parse_rsync_output(proc.stdout)
    result = RsyncResult(
        project_name=project.name,
        return_code=proc.returncode,
        transferred=transferred,
        deleted=deleted,
        dry_run=dry_run,
        raw_stdout=proc.stdout,
        raw_stderr=proc.stderr,
    )
    if proc.returncode != 0:
        raise RsyncError(
            f"rsync exited {proc.returncode} for project "
            f"{project.name!r}: {proc.stderr.strip() or '(no stderr)'}",
            result=result,
        )
    return result


# ─── output parsing ─────────────────────────────────────────────────────────


def parse_rsync_output(stdout: str) -> tuple[int, int]:
    """Parse `rsync -av` stdout to count transfers + deletions.

    Heuristics (matches the original brain-sync behavior):
        - "transferred" lines: any line ending in `.md` that does NOT
          start with "deleting " (rsync's verbose mode lists each file).
        - "deleted" lines: any line starting with "deleting " (whether
          --delete is in effect or not, rsync emits this prefix).

    These heuristics are stable across rsync 2.x and 3.x but break if
    rsync changes its verbose-output prefix. Documented as a known
    fragility — replace with `--info=stats2` parsing in v1.x if it
    becomes a problem.
    """
    transferred = 0
    deleted = 0
    for line in stdout.splitlines():
        if line.startswith("deleting "):
            deleted += 1
        elif line.endswith(".md"):
            transferred += 1
    return transferred, deleted
