"""Shared utilities for asof:sync.

Slug normalization, path-safety checks, atomic writes, file locking,
skill-version discovery, mtime helpers. Stdlib only (Python 3.9+).

These helpers exist so the rest of the skill (config / delta / rsync_runner /
report / sync) can rely on production-grade primitives without re-implementing
common patterns. Each function has a single responsibility, raises explicit
errors with actionable messages, and is independently testable.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

# ─── version discovery ──────────────────────────────────────────────────────
#
# The skill needs to know its own version at runtime to apply the four-cell
# schema-version compatibility matrix (PLAN.md section 2). The single source
# of truth is `.claude-plugin/plugin.json` at the plugin root.
#
# Discovery walks up from this file's location until a manifest is found.
# Tests can override via the `ASOF_SKILL_VERSION_OVERRIDE` env var.


def _read_skill_version() -> str:
    """Read `version` from `.claude-plugin/plugin.json`.

    Walks up from this file's location looking for the manifest. If none is
    found (e.g. the skill was installed loose, not as a plugin), returns
    `"0.0.0-dev"` as a defensive default.

    Honors `ASOF_SKILL_VERSION_OVERRIDE` env var (used by tests).
    """
    if env := os.environ.get("ASOF_SKILL_VERSION_OVERRIDE"):
        return env
    here = Path(__file__).resolve()
    for parent in here.parents:
        manifest = parent / ".claude-plugin" / "plugin.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                v = data.get("version")
                if isinstance(v, str) and v:
                    return v
            except (json.JSONDecodeError, OSError):
                # Manifest exists but is malformed — fall through to default.
                # The caller (config.version_compat) will surface a clearer
                # error if version comparison fails.
                pass
    return "0.0.0-dev"


SKILL_VERSION: str = _read_skill_version()


# ─── slug / path safety ─────────────────────────────────────────────────────
#
# Project names are user input. They flow into directory paths under
# <wiki_dir>/raw/ and <wiki_dir>/wiki/, so they must be sanitized to prevent
# path traversal (`../`, absolute paths, NUL bytes, etc.).
#
# Slug rules: lowercase ASCII letters, digits, and hyphens. 1–64 chars.
# Must start and end with [a-z0-9].

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def slugify(name: str) -> str:
    """Normalize a project name to a safe slug, or raise.

    Rules: lowercase, ASCII alphanumerics and `-` only, 1–64 chars,
    starts/ends with `[a-z0-9]`.

    Raises ValueError on:
        - non-string / empty / whitespace-only input
        - input containing path separators or `..`
        - input that normalizes to an empty / out-of-range slug

    Examples:
        >>> slugify("My Project")
        'my-project'
        >>> slugify("traddea")
        'traddea'
        >>> slugify("../etc")
        Traceback (most recent call last):
            ...
        ValueError: project name '../etc' contains path separators
    """
    if not isinstance(name, str):
        raise ValueError(f"project name must be a string, got {type(name).__name__}")
    if not name.strip():
        raise ValueError("project name must be non-empty")
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise ValueError(f"project name {name!r} contains path separators or NUL")
    slug = name.strip().lower()
    # Replace any run of non-allowed chars with a single hyphen.
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if not slug:
        raise ValueError(f"project name {name!r} normalizes to empty slug")
    if len(slug) > 64:
        raise ValueError(f"project name {name!r} exceeds 64 chars after slugify")
    if not _SLUG_RE.match(slug):
        # Belt-and-suspenders: should be unreachable given the regex
        # construction above, but explicit check guards against subtle
        # bugs (e.g. unicode look-alikes that survived earlier filtering).
        raise ValueError(f"slug {slug!r} failed validation")
    return slug


def ensure_inside(child: Path | str, parent: Path | str) -> Path:
    """Resolve `child` and verify it lives inside `parent`.

    Returns the resolved absolute path. Raises ValueError if `child` escapes
    `parent` via `..`, symlinks, or absolute path injection.

    Used at every filesystem boundary in the skill (project subdir creation,
    config writes, lock files, etc.) to enforce the containment invariant
    that user input cannot reach outside `<wiki_dir>`.
    """
    child_resolved = Path(child).resolve()
    parent_resolved = Path(parent).resolve()
    try:
        child_resolved.relative_to(parent_resolved)
    except ValueError as exc:
        raise ValueError(
            f"path {child!s} resolves to {child_resolved!s} which is outside "
            f"{parent_resolved!s}"
        ) from exc
    return child_resolved


# ─── atomic write ───────────────────────────────────────────────────────────
#
# Last-sync reports and other small JSON files must survive a process kill
# mid-write. Pattern: write to a temp file in the same directory (so the
# rename is atomic on POSIX), then os.replace() into place.


def atomic_write_json(path: Path | str, data: Any) -> None:
    """Write `data` to `path` as JSON atomically.

    Strategy:
        1. Create temp file in the target's parent directory (same filesystem
           guarantees os.replace is atomic).
        2. Write JSON content + trailing newline.
        3. os.replace(temp, path) — atomic on POSIX.
        4. On any error, remove the temp file and re-raise.

    Creates parent directories as needed. JSON is indented and key-sorted
    for human-readable diffs.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        Path(tmp).replace(target)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            Path(tmp).unlink()
        raise


def atomic_write_text(path: Path | str, content: str) -> None:
    """Write `content` to `path` as UTF-8 text atomically.

    Same temp-then-rename pattern as `atomic_write_json` so a process kill
    mid-write leaves either the old file or the new one — never a partial
    half-written markdown / gitignore / CLAUDE.md. Creates parent dirs.

    Codex round-1 phase-3 MEDIUM: scaffold.py's docstring promised every
    write was atomic, but only the .asof.json went through the atomic
    helper — the four bookkeeping templates, the wiki-root CLAUDE.md,
    and the Pattern C .gitignore augment all used plain `Path.write_text`,
    which on POSIX is two syscalls (open+truncate, then write). A SIGKILL
    between those produced an empty file.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f"{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp).replace(target)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            Path(tmp).unlink()
        raise


# ─── file lock ──────────────────────────────────────────────────────────────
#
# Sync and lint must not run concurrently against the same wiki dir
# (Codex review round 1: "concurrency and locking are missing").
#
# We use POSIX advisory locks (fcntl.flock). Locks release on process exit
# or kill — no leak. The hook also checks the lock and degrades gracefully
# (emits a "sync in progress" reminder instead of attempting rsync).


@contextlib.contextmanager
def file_lock(path: Path | str, blocking: bool = True) -> Iterator[None]:
    """Acquire an advisory file lock for the duration of the with-block.

    Args:
        path: lockfile path (created if missing).
        blocking: if True (default), waits for the lock. If False, raises
            BlockingIOError immediately when another holder has it.

    The lockfile is opened in 'w' mode; we don't write content to it, only
    use it as a kernel lock target. Lock auto-releases on process exit
    (kernel-managed) so a killed sync doesn't leave a stuck lock.

    Example:
        with file_lock(wiki_dir / ".asof.lock"):
            # critical section: rsync, write last-sync report, etc.
            ...
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = fcntl.LOCK_EX if blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
    fh = target.open("w")
    try:
        fcntl.flock(fh.fileno(), flags)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


# ─── mtime helpers ──────────────────────────────────────────────────────────
#
# `source_mtime` in wiki frontmatter is always an ISO date string
# (YYYY-MM-DD). We use the file's local-time mtime for human readability;
# the wiki's "as of" semantics work with date precision (not time precision).


def get_mtime_iso(path: Path | str) -> str:
    """Return the file's mtime as an ISO date string (YYYY-MM-DD).

    Uses local timezone via `date.fromtimestamp`. Returns dates suitable
    for direct insertion into wiki frontmatter `source_mtime` fields.
    """
    return date.fromtimestamp(Path(path).stat().st_mtime).isoformat()


# ─── version comparison ─────────────────────────────────────────────────────
#
# Used by the schema-version compatibility matrix. Versions are semver-like
# strings ("1.0.0", "0.1.0-dev", "1.2.3-rc1"). We strip pre-release suffixes
# for comparison purposes (a pragmatic choice — pre-releases of the same
# X.Y.Z compare as equal).
#
# Returns a tuple of ints suitable for direct comparison with `<`, `==`, etc.


def parse_version(v: str) -> tuple[int, ...]:
    """Parse a semver-like string into a tuple of ints for comparison.

    Strips pre-release / build suffixes after the first `-` or `+`.
    Empty / malformed segments raise ValueError.

    Examples:
        >>> parse_version("1.0.0")
        (1, 0, 0)
        >>> parse_version("0.1.0-dev")
        (0, 1, 0)
        >>> parse_version("1.2.3-rc1+sha.abc")
        (1, 2, 3)
    """
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"version must be a non-empty string, got {v!r}")
    # Split off pre-release / build metadata.
    core = re.split(r"[-+]", v.strip(), maxsplit=1)[0]
    parts = core.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"version {v!r} contains non-integer segment") from exc


def compare_versions(a: str, b: str) -> int:
    """Return -1, 0, or 1 for `a` <, ==, > `b` after stripping pre-release.

    Handles different segment counts by zero-padding (1.0 == 1.0.0).
    """
    pa, pb = parse_version(a), parse_version(b)
    n = max(len(pa), len(pb))
    pa = pa + (0,) * (n - len(pa))
    pb = pb + (0,) * (n - len(pb))
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0
