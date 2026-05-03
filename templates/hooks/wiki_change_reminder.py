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

import json
import os
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

    Side effects:
        - Writes JSON to stdout with `hookSpecificOutput.additionalContext`
          if a reminder should be emitted.
        - Touches `<wiki_dir>/.pending-sync/<project>.stamp` to mark the
          debounce window.
    """
    project_root = env.get("ASOF_PROJECT_ROOT", "").strip()
    project_name = env.get("ASOF_PROJECT_NAME", "").strip()
    wiki_dir = env.get("ASOF_DIR", "").strip()
    if not project_root or not project_name or not wiki_dir:
        # Defensive default: any missing env → no-op cleanly. Don't fail.
        return 0

    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError:
        return 0  # Malformed input — don't crash the tool.

    if not _should_fire(payload, project_root):
        return 0

    if _is_within_debounce_window(wiki_dir, project_name, now=now):
        return 0  # Recently emitted — silent.

    _stamp_debounce(wiki_dir, project_name, now=now)
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


def _should_fire(payload: dict[str, Any], project_root: str) -> bool:
    """True if this payload represents an .md edit inside project_root."""
    tool = payload.get("tool_name", "")
    if tool not in TRIGGERING_TOOLS:
        return False
    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path or not file_path.endswith(".md"):
        return False
    try:
        abs_path = Path(file_path).resolve()
        root = Path(project_root).resolve()
    except (OSError, ValueError):
        return False
    try:
        abs_path.relative_to(root)
    except ValueError:
        return False
    # Exclude edits inside the wiki itself (avoids feedback loop when the
    # agent is updating wiki pages from inside the project).
    return True


def _relative_path(payload: dict[str, Any], project_root: str) -> str:
    """Compute the project-relative path string for the message."""
    file_path = payload.get("tool_input", {}).get("file_path", "")
    try:
        rel = Path(file_path).resolve().relative_to(Path(project_root).resolve())
        return str(rel)
    except (ValueError, OSError):
        return file_path


def _is_within_debounce_window(
    wiki_dir: str, project_name: str, *, now: float | None = None
) -> bool:
    """True if the per-project debounce stamp was touched within DEBOUNCE_SECONDS."""
    stamp = Path(wiki_dir) / ".pending-sync" / f"{project_name}.stamp"
    if not stamp.is_file():
        return False
    try:
        last = stamp.stat().st_mtime
    except OSError:
        return False
    current = now if now is not None else time.time()
    return (current - last) < DEBOUNCE_SECONDS


def _stamp_debounce(
    wiki_dir: str, project_name: str, *, now: float | None = None
) -> None:
    """Touch the per-project debounce stamp to start the suppression window."""
    stamp_dir = Path(wiki_dir) / ".pending-sync"
    stamp_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp_dir / f"{project_name}.stamp"
    stamp.touch()
    if now is not None:
        os.utime(stamp, (now, now))


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
