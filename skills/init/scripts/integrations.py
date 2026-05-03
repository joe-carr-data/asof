"""asof:init stage 5 — apply the four integrations the user opted into.

Each integration is independent and idempotent (safe to re-run). Failures
in one integration are recorded in IntegrationResult but do not abort the
others — partial success is preferable to all-or-nothing because users may
have opted into specific subsets.

Four integrations:

1. **Append CLAUDE.md snippet** — read templates/project_CLAUDE_snippet.md,
   substitute placeholders, append to <project_root>/CLAUDE.md. The snippet
   is marker-fenced (`<!-- asof-wiki:precedence-block -->` ...
   `<!-- /asof-wiki:precedence-block -->`); existing blocks are detected
   and the operation is skipped (no duplication on re-run).

2. **Install the change-reminder hook** — copy
   templates/hooks/wiki_change_reminder.py into
   <project_root>/.claude/hooks/. Register in the settings file (see #3).

3. **Update settings file** — merge a PostToolUse hook entry +
   permissions.additionalDirectories entry into either
   <project_root>/.claude/settings.local.json (default — gitignored,
   machine-portable absolute paths) or .claude/settings.json
   (--commit-settings opt-in). Existing keys preserved; merge, never
   overwrite.

4. **Run first sync** — shell out to skills/sync/scripts/sync.py with
   --non-interactive --project <slug>. Captures exit code so init can
   surface failure to the user without raising.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import sys
from pathlib import Path

from _sync_bridge import atomic_write_json, atomic_write_text
from scaffold import (
    load_template,
    render_template,
    resolve_template_dir,
)
from wizard import IntegrationChoice, LayoutChoice

#: Marker comments wrapping the wiki-precedence block in project CLAUDE.md.
#: Used to detect existing installations on re-run.
SNIPPET_OPEN_MARKER = "<!-- asof-wiki:precedence-block -->"
SNIPPET_CLOSE_MARKER = "<!-- /asof-wiki:precedence-block -->"

#: Hook matcher for PostToolUse — fires on the four file-modifying tools.
HOOK_MATCHER = "Write|Edit|MultiEdit|NotebookEdit"

#: Filename written into <project>/.claude/hooks/.
HOOK_SCRIPT_FILENAME = "asof_wiki_change_reminder.py"

#: Project-CLAUDE.md template filename inside the plugin's templates/.
CLAUDE_SNIPPET_TEMPLATE = "project_CLAUDE_snippet.md"

#: Hook source filename inside the plugin's templates/hooks/.
HOOK_SOURCE_FILENAME = "wiki_change_reminder.py"


# ─── data model ────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class IntegrationRequest:
    """Inputs to apply_integrations()."""

    layout: LayoutChoice
    project_slug: str  # already validated upstream by ScaffoldRequest
    project_display_name: str
    project_root: Path  # absolute; for Pattern A/B = source; for C = wiki_dir.parent
    choices: IntegrationChoice


@dataclasses.dataclass(frozen=True)
class IntegrationResult:
    """What apply_integrations() actually did.

    Per-step errors recorded in `errors` (Codex round-1 phase-3 HIGH 2):
    each step runs independently; if one fails, later steps still run.
    `errors` is a tuple of (step_name, error_message) for every step
    that raised. The corresponding boolean (snippet_appended /
    hook_installed / etc.) stays False so callers can detect partial
    success.
    """

    snippet_appended: bool
    snippet_skipped_already_present: bool
    hook_installed: bool
    hook_skipped_already_present: bool
    settings_path: Path | None
    additional_dir_added: bool
    additional_dir_already_present: bool
    first_sync_ran: bool
    first_sync_exit_code: int | None
    errors: tuple[tuple[str, str], ...]
    dry_run: bool

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ─── 1) CLAUDE.md snippet ──────────────────────────────────────────────────


def append_claudemd_snippet(
    request: IntegrationRequest, *, today: str, dry_run: bool = False
) -> tuple[bool, bool]:
    """Append the wiki-precedence snippet to <project_root>/CLAUDE.md.

    Returns: (appended, skipped_already_present).

    Marker-fence detection: if the file contains the open or close marker,
    we assume the snippet (or some marker-bearing variant) is already
    present and skip rather than risk duplicating or interleaving blocks.
    """
    target = request.project_root / "CLAUDE.md"
    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        if SNIPPET_OPEN_MARKER in existing or SNIPPET_CLOSE_MARKER in existing:
            return (False, True)

    rendered = render_template(
        load_template(CLAUDE_SNIPPET_TEMPLATE),
        {
            "WIKI_DIR": str(request.layout.wiki_dir),
            "PROJECT_NAME": request.project_display_name,
            "PROJECT_SLUG": request.project_slug,
            "TODAY": today,
            "ASOF_VERSION": _read_skill_version_for_template(),
        },
    )

    if dry_run:
        return (True, False)

    # Append with a leading blank-line separator if the file already has
    # content. Create the file if it doesn't exist.
    if target.is_file():
        existing = target.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing = existing + "\n"
        new_content = existing + "\n" + rendered
    else:
        new_content = rendered
    atomic_write_text(target, new_content)
    return (True, False)


def _read_skill_version_for_template() -> str:
    """Lazy SKILL_VERSION lookup for templates that mention asof's version."""
    # Imported through the bridge for the single-source-of-truth semantics.
    from _sync_bridge import SKILL_VERSION

    return SKILL_VERSION


# ─── 2) Hook script ────────────────────────────────────────────────────────


def install_hook(
    request: IntegrationRequest, *, dry_run: bool = False
) -> tuple[bool, bool]:
    """Copy the change-reminder hook into <project>/.claude/hooks/.

    Returns: (installed, skipped_already_present).
    """
    hook_dir = request.project_root / ".claude" / "hooks"
    target = hook_dir / HOOK_SCRIPT_FILENAME
    if target.is_file():
        return (False, True)

    if dry_run:
        return (True, False)

    hook_dir.mkdir(parents=True, exist_ok=True)
    source = resolve_template_dir() / "hooks" / HOOK_SOURCE_FILENAME
    if not source.is_file():
        raise FileNotFoundError(
            f"asof:init: hook source {source!s} not found in plugin templates"
        )
    shutil.copy2(source, target)
    target.chmod(0o755)  # ensure executable for the hook runtime
    return (True, False)


# ─── 3) settings.json / settings.local.json ────────────────────────────────


def _settings_path(request: IntegrationRequest) -> Path:
    """Pick which settings file to edit.

    Default: settings.local.json (gitignored, machine-portable absolute paths
    don't pollute commits — gpt-5.2-pro round-2 phase-3 advice).
    Opt-in: settings.json via --commit-settings.
    """
    name = (
        "settings.json"
        if request.choices.commit_settings
        else "settings.local.json"
    )
    return request.project_root / ".claude" / name


def update_settings(
    request: IntegrationRequest, *, dry_run: bool = False
) -> tuple[Path, bool, bool]:
    """Merge wiki integrations into the chosen settings file.

    Returns: (path, additional_dir_added, additional_dir_already_present).

    Two merges (each guarded against duplicates):
      - permissions.additionalDirectories: add layout.wiki_dir if absent
        (skipped for Pattern C — wiki is inside the repo, no `--add-dir`
        needed).
      - hooks.PostToolUse: add a hook entry pointing at the installed hook
        script with the right `env` block. Skipped if a hook entry with
        the same command already exists.

    The file is written atomically. Existing keys are preserved.
    """
    path = _settings_path(request)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"asof:init: existing settings file {path!s} is not valid JSON: "
                f"{exc.msg} (line {exc.lineno}). Fix it manually before re-running."
            ) from exc
    if not isinstance(existing, dict):
        raise RuntimeError(
            f"asof:init: existing settings file {path!s} top-level must be a "
            f"JSON object; got {type(existing).__name__}."
        )

    # Merge additionalDirectories (only if requested, i.e. Pattern A/B).
    additional_added = False
    additional_already_present = False
    if request.choices.add_additional_directories:
        permissions = existing.setdefault("permissions", {})
        if not isinstance(permissions, dict):
            raise RuntimeError(
                f"asof:init: settings.permissions must be a JSON object in "
                f"{path!s}"
            )
        dirs = permissions.setdefault("additionalDirectories", [])
        if not isinstance(dirs, list):
            raise RuntimeError(
                f"asof:init: settings.permissions.additionalDirectories must "
                f"be a JSON array in {path!s}"
            )
        wiki_dir_str = str(request.layout.wiki_dir)
        if wiki_dir_str in dirs:
            additional_already_present = True
        else:
            dirs.append(wiki_dir_str)
            additional_added = True

    # Merge hooks.PostToolUse (only if hook was opted in).
    if request.choices.install_hook:
        _merge_post_tool_use_hook(existing, request)

    if not dry_run:
        atomic_write_json(path, existing)

    return (path, additional_added, additional_already_present)


def _merge_post_tool_use_hook(
    settings: dict, request: IntegrationRequest
) -> None:
    """Merge the asof PostToolUse hook entry into settings['hooks']['PostToolUse'].

    Detects an existing entry by matching the command path. If found, skips
    (idempotent). Otherwise appends a new entry with the matcher + command +
    env block needed by templates/hooks/wiki_change_reminder.py.
    """
    hook_command = str(
        request.project_root / ".claude" / "hooks" / HOOK_SCRIPT_FILENAME
    )
    hook_env = {
        "ASOF_PROJECT_ROOT": str(request.project_root),
        "ASOF_PROJECT_NAME": request.project_slug,
        "ASOF_DIR": str(request.layout.wiki_dir),
    }

    hooks_block = settings.setdefault("hooks", {})
    if not isinstance(hooks_block, dict):
        raise RuntimeError(
            "asof:init: settings.hooks must be a JSON object"
        )
    post_tool_use = hooks_block.setdefault("PostToolUse", [])
    if not isinstance(post_tool_use, list):
        raise RuntimeError(
            "asof:init: settings.hooks.PostToolUse must be a JSON array"
        )

    # Detect duplicate: an entry whose hooks[].command matches our path.
    for entry in post_tool_use:
        inner = entry.get("hooks", []) if isinstance(entry, dict) else []
        if any(
            isinstance(h, dict) and h.get("command") == hook_command
            for h in inner
        ):
            return  # already installed; idempotent skip

    post_tool_use.append(
        {
            "matcher": HOOK_MATCHER,
            "hooks": [
                {
                    "type": "command",
                    "command": hook_command,
                    "env": hook_env,
                }
            ],
        }
    )


# ─── 4) First sync ────────────────────────────────────────────────────────


def run_first_sync(
    request: IntegrationRequest, *, dry_run: bool = False
) -> tuple[bool, int | None]:
    """Invoke `asof:sync --project <slug> --non-interactive` as a subprocess.

    Returns: (ran, exit_code). Exit-code None when not run (dry-run path or
    subprocess never started). Failures (non-zero exit) are surfaced via
    the exit_code so init can decide what to tell the user; we never raise
    from this function.

    Subprocess is preferred over an in-process call because it gives sync
    a clean argparse environment + isolates any stdio behavior.
    """
    if dry_run:
        return (False, None)

    sync_script = (
        Path(__file__).resolve().parent.parent.parent
        / "sync"
        / "scripts"
        / "sync.py"
    )
    if not sync_script.is_file():
        return (False, None)

    proc = subprocess.run(
        [
            sys.executable,
            str(sync_script),
            "--project",
            request.project_slug,
            "--non-interactive",
            "--wiki-dir",
            str(request.layout.wiki_dir),
        ],
        capture_output=False,
        text=True,
        check=False,
    )
    return (True, proc.returncode)


# ─── orchestrator ──────────────────────────────────────────────────────────


def apply_integrations(
    request: IntegrationRequest, *, today: str, dry_run: bool = False
) -> IntegrationResult:
    """Run every requested integration. Failures in one don't abort others.

    Per-step exception capture (Codex round-1 phase-3 HIGH 2): each step
    runs in its own try/except. OSError, RuntimeError, FileNotFoundError,
    and PermissionError are recorded into the `errors` tuple and the
    corresponding boolean stays False; the next step still runs. Other
    exceptions (TypeError, AttributeError, etc. — programmer bugs) still
    propagate so they don't get silently swallowed.

    PLAN.md §316 specifies this partial-failure behavior. Previous code
    let any unexpected OSError abort the remaining integrations.

    Order matters:
      1. Snippet first (no side effects beyond the user's CLAUDE.md).
      2. Hook script copy (prerequisite for hook entry in settings).
      3. Settings update (depends on hook script being in place).
      4. First sync last (depends on .asof.json + raw/ existing).
    """
    errors: list[tuple[str, str]] = []

    snippet_appended = False
    snippet_skipped = False
    if request.choices.install_claudemd_snippet:
        try:
            snippet_appended, snippet_skipped = append_claudemd_snippet(
                request, today=today, dry_run=dry_run
            )
        except (OSError, RuntimeError) as exc:
            errors.append(("CLAUDE.md snippet", _format_step_error(exc)))

    hook_installed = False
    hook_skipped = False
    if request.choices.install_hook:
        try:
            hook_installed, hook_skipped = install_hook(
                request, dry_run=dry_run
            )
        except (OSError, RuntimeError) as exc:
            errors.append(("hook install", _format_step_error(exc)))

    settings_path: Path | None = None
    additional_added = False
    additional_already_present = False
    if (
        request.choices.add_additional_directories
        or request.choices.install_hook
    ):
        try:
            settings_path, additional_added, additional_already_present = (
                update_settings(request, dry_run=dry_run)
            )
        except (OSError, RuntimeError) as exc:
            errors.append(("settings update", _format_step_error(exc)))

    first_sync_ran = False
    first_sync_exit: int | None = None
    if request.choices.run_first_sync:
        try:
            first_sync_ran, first_sync_exit = run_first_sync(
                request, dry_run=dry_run
            )
        except (OSError, RuntimeError) as exc:
            errors.append(("first sync", _format_step_error(exc)))

    return IntegrationResult(
        snippet_appended=snippet_appended,
        snippet_skipped_already_present=snippet_skipped,
        hook_installed=hook_installed,
        hook_skipped_already_present=hook_skipped,
        settings_path=settings_path,
        additional_dir_added=additional_added,
        additional_dir_already_present=additional_already_present,
        first_sync_ran=first_sync_ran,
        first_sync_exit_code=first_sync_exit,
        errors=tuple(errors),
        dry_run=dry_run,
    )


def _format_step_error(exc: BaseException) -> str:
    """One-line error string for the per-step error log."""
    return f"{type(exc).__name__}: {exc}"
