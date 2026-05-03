#!/usr/bin/env python3
"""asof — PostToolUse hook: wiki change reminder.

Installed by `asof:init` (opt-in) into a user project's `.claude/hooks/`.
Fires after every `Write` / `Edit` / `MultiEdit` / `NotebookEdit` on a `*.md`
file inside `$ASOF_PROJECT_ROOT`. Emits a non-blocking reminder that the
asof wiki may now be stale.

Hook contract: PostToolUse + exit 0 + JSON `hookSpecificOutput.additionalContext`
(verified against the official Claude Code hooks docs). Per-project debounce
via `<wiki_dir>/.pending-sync/<project>.stamp` — 30 s suppression window so a
50-file MultiEdit doesn't fire 50 reminders.

Required env (set by `asof:init` in `.claude/settings.json`):
    ASOF_PROJECT_ROOT  — absolute path to the user's project repo.
    ASOF_PROJECT_NAME  — slugified project name (per .asof.json).
    ASOF_DIR           — wiki dir (where .pending-sync/ + .asof.lock live).

If any of those is unset, the hook silently no-ops.

Exit codes:
    0 always (PostToolUse never fails because of the hook itself).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

#: Files matching these tool names trigger the hook.
TRIGGERING_TOOLS: frozenset[str] = frozenset(
    {"Write", "Edit", "MultiEdit", "NotebookEdit"}
)

#: Suppression window: don't emit two reminders for the same project within
#: this many seconds. Codex round-2 fix (per-project, not global).
DEBOUNCE_SECONDS: float = 30.0

#: Slug regex matching the format produced by `slugify()` in the sync skill.
#: Used to validate `ASOF_PROJECT_NAME` env (which lands in a filesystem
#: path) against path-traversal attacks. gpt-5.2-pro round-2 phase-2 HIGH.
_PROJECT_SLUG_RE: re.Pattern[str] = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
)


def main(
    stdin_text: str,
    env: dict[str, str],
    *,
    now: float | None = None,
) -> int:
    """PostToolUse hook entry point.

    Args:
        stdin_text: the raw JSON payload Claude Code piped to the hook.
        env: env vars dict (test seam — pass `os.environ` in production).
        now: override for time.time() (test seam for debounce timing).

    Returns:
        Exit code (always 0 for hooks; non-zero would surface as an error
        in PostToolUse, which we don't want for a benign reminder).
        Wrapped in try/except so filesystem errors (unwritable wiki_dir,
        invalid path, etc.) never propagate as a non-zero exit.

    Side effects:
        - Writes JSON to stdout with `hookSpecificOutput.additionalContext`
          if a reminder should be emitted (and we won the debounce claim).
        - Atomically claims `<wiki_dir>/.pending-sync/<project>.stamp`
          via O_EXCL — concurrent invocations from a 50-file MultiEdit
          all race against this single atomic creation; only one wins
          and emits.
    """
    try:
        return _main_inner(stdin_text, env, now=now)
    except Exception:  # noqa: BLE001 — defensive top-level guard
        # PostToolUse contract: never raise. Any internal failure
        # (missing perms, malformed env path, etc.) becomes a silent
        # no-op so the user's tool call isn't surfaced as a hook error.
        return 0


def _main_inner(
    stdin_text: str,
    env: dict[str, str],
    *,
    now: float | None = None,
) -> int:
    project_root = env.get("ASOF_PROJECT_ROOT", "").strip()
    project_name = env.get("ASOF_PROJECT_NAME", "").strip()
    wiki_dir = env.get("ASOF_DIR", "").strip()
    if not project_root or not project_name or not wiki_dir:
        # Defensive default: any missing env → no-op cleanly. Don't fail.
        return 0

    # Validate project_name against slug regex BEFORE using it as a path
    # component (gpt-5.2-pro round-2 phase-2 HIGH: untrusted env value
    # could escape .pending-sync/ via "../../foo"). The hook never trusts
    # env to construct paths without explicit containment.
    if not _PROJECT_SLUG_RE.match(project_name):
        return 0

    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return 0  # Malformed input — don't crash the tool.

    if not _should_fire(payload, project_root, wiki_dir):
        return 0

    # Atomic debounce claim (Codex round-1 phase-2 HIGH fix). Replaces the
    # TOCTOU "check then stamp" pattern that let parallel hook invocations
    # all observe "no stamp" and emit. O_EXCL gives at-most-one winner per
    # debounce window; runners-up either find a fresh stamp (suppressed)
    # or refresh a stale stamp (emit, then suppress further N seconds).
    if not _claim_debounce_slot(wiki_dir, project_name, now=now):
        return 0

    rel_path = _relative_path(payload, project_root)
    sync_in_progress = _lock_held(wiki_dir)
    message = _build_message(project_name, rel_path, wiki_dir, sync_in_progress)

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }
    }
    sys.stdout.write(json.dumps(out))
    return 0


# ─── helpers ────────────────────────────────────────────────────────────────


def _should_fire(
    payload: dict[str, Any], project_root: str, wiki_dir: str
) -> bool:
    """True if this payload represents an .md edit inside project_root that
    is NOT also inside the wiki dir.

    Excluding edits under wiki_dir prevents a Pattern-C feedback loop: the
    wiki lives at <repo>/.asof/, so editing wiki pages would otherwise
    trigger sync reminders during the agent's own ingest work
    (gpt-5.2-pro round-2 phase-2 HIGH). For Pattern A/B wikis, this check
    is a no-op since wiki_dir is outside project_root.
    """
    tool = payload.get("tool_name", "")
    if tool not in TRIGGERING_TOOLS:
        return False
    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path or not file_path.endswith(".md"):
        return False
    try:
        abs_path = Path(file_path).resolve()
        root = Path(project_root).resolve()
        wiki = Path(wiki_dir).resolve()
    except (OSError, ValueError):
        return False
    # Must be inside the project root...
    try:
        abs_path.relative_to(root)
    except ValueError:
        return False
    # ...but NOT inside the wiki dir (Pattern C feedback-loop guard).
    try:
        abs_path.relative_to(wiki)
        return False  # File is inside the wiki — skip
    except ValueError:
        pass  # Not inside wiki — good
    return True


def _relative_path(payload: dict[str, Any], project_root: str) -> str:
    """Compute the project-relative path string for the message."""
    file_path = payload.get("tool_input", {}).get("file_path", "")
    try:
        rel = Path(file_path).resolve().relative_to(Path(project_root).resolve())
        return str(rel)
    except (ValueError, OSError):
        return file_path


def _claim_debounce_slot(
    wiki_dir: str, project_name: str, *, now: float | None = None
) -> bool:
    """Atomically claim the per-project debounce slot.

    Returns True if this caller wins the slot (and should emit a reminder),
    False if another caller already holds it within the suppression window.

    Uses O_EXCL exclusive-creation semantics so parallel hook invocations
    from a single MultiEdit batch race deterministically against one another:
    exactly one process succeeds in creating the stamp file; the others
    observe `FileExistsError` and either:

      - find a fresh stamp (within DEBOUNCE_SECONDS) and suppress, or
      - find a stale stamp and refresh it (emit + reset window).

    Edge case: at the boundary between "stale" and "fresh" two callers
    may both refresh and emit — acceptable; the cost is one extra
    reminder, never silent suppression of a real change.

    Test seam: `now` overrides `time.time()` for deterministic tests.

    NOTE: `project_name` is assumed to have already been slug-validated
    by `_main_inner`. We additionally verify the resolved stamp path lives
    directly under stamp_dir as a belt-and-suspenders containment check
    (gpt-5.2-pro round-2 phase-2 HIGH).
    """
    base = Path(wiki_dir).resolve()
    stamp_dir = (base / ".pending-sync").resolve()
    stamp_dir.mkdir(parents=True, exist_ok=True)

    stamp = (stamp_dir / f"{project_name}.stamp").resolve()
    # Containment: even though _PROJECT_SLUG_RE rejects path-separator
    # input, double-check the resolved stamp is a direct child of
    # stamp_dir (defense in depth).
    if stamp.parent != stamp_dir:
        return False

    current = now if now is not None else time.time()

    try:
        # O_EXCL creates the file or raises FileExistsError — atomic.
        fd = os.open(stamp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd)
        os.utime(stamp, (current, current))
        return True  # We won the race.
    except FileExistsError:
        try:
            last = stamp.stat().st_mtime
        except OSError:
            return True  # Can't read stamp — permissive emit.
        if (current - last) < DEBOUNCE_SECONDS:
            return False  # Fresh stamp held by another caller — suppress.
        # Stale stamp — refresh and emit. (Two parallel callers may both
        # do this; both emit. Acceptable — the alternative is silent
        # suppression of a real change after a long quiet period.)
        with contextlib.suppress(OSError):
            os.utime(stamp, (current, current))
        return True


def _lock_held(wiki_dir: str) -> bool:
    """Heuristic: is `<wiki_dir>/.asof.lock` currently held by a sync run?

    We can't `flock` to check without acquiring; instead we use the file's
    presence + recent mtime as a heuristic. If it was modified in the last
    five minutes, assume a sync is in progress. False positives are benign
    (we just append a "(sync in progress)" hint to the reminder).
    """
    lock = Path(wiki_dir) / ".asof.lock"
    if not lock.is_file():
        return False
    try:
        return (time.time() - lock.stat().st_mtime) < 300.0
    except OSError:
        return False


def _build_message(
    project_name: str, rel_path: str, wiki_dir: str, sync_in_progress: bool
) -> str:
    base = (
        f"[asof:wiki-reminder] {rel_path} was just edited in project "
        f"{project_name!r}. The wiki at {wiki_dir} may now be stale for "
        f"this doc. If this change is substantive (new doc, reworded "
        f"section, status flip), consider running /asof:sync "
        f"{project_name} before ending the turn."
    )
    if sync_in_progress:
        base += " (sync in progress — your changes will be picked up)"
    return base


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read(), dict(os.environ)))
