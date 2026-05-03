"""asof:init stages 2 + 5 — interactive prompts.

Two roles:
    Stage 2 (layout choice): ask the user which wiki-dir pattern to use
        (A: shared wiki under home, B: per-project under home, C: in-repo).
        Captures the wiki_dir path and (for A/B) the source path.
    Stage 5 (integrations): four yes/no questions — append the
        wiki-precedence snippet to project CLAUDE.md, install the PostToolUse
        change-reminder hook, add wiki_dir to the project's
        `additionalDirectories`, run a first sync immediately.

Both stages honor `--non-interactive` / `--yes` / `ASOF_NON_INTERACTIVE=1`
by accepting the documented defaults instead of prompting. CI / scripted
setups need this to run end-to-end without a TTY.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from _sync_bridge import DEFAULT_WIKI_DIR

#: Sentinel returned by interactive prompts when the user aborts (Ctrl-C,
#: Ctrl-D, or empty input on a question that doesn't have an empty default).
ABORTED = object()

#: Default layout pattern when running non-interactively without --pattern.
#: Pattern A is the most common case (shared wiki under home, multi-project).
DEFAULT_PATTERN: Literal["A", "B", "C"] = "A"


# ─── data model ────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class LayoutChoice:
    """The user's resolved stage-2 selection.

    `wiki_dir` is the absolute resolved path. `source` is None for Pattern C
    (auto-derived as wiki_dir.parent at sync time per SCHEMA rules).
    """

    pattern: Literal["A", "B", "C"]
    wiki_dir: Path
    source: Path | None  # None for Pattern C


@dataclasses.dataclass(frozen=True)
class IntegrationChoice:
    """The user's resolved stage-5 selections.

    Defaults documented so `--non-interactive` runs are predictable:
        - install_claudemd_snippet: True (most users want the precedence rule)
        - install_hook: True (proactive sync reminders are valuable)
        - add_additional_directories: True (so the wiki is readable from the
          project's Claude Code session)
        - run_first_sync: True (immediate feedback that init worked)
        - commit_settings: False (writes to .claude/settings.local.json,
          gitignored — machine-portable; --commit-settings opts into
          .claude/settings.json which is committed)

    Pattern C overrides `add_additional_directories` to False because the
    wiki is already inside the source repo (no `--add-dir` needed).
    """

    install_claudemd_snippet: bool
    install_hook: bool
    add_additional_directories: bool
    run_first_sync: bool
    commit_settings: bool


# ─── primitives ────────────────────────────────────────────────────────────


def is_non_interactive(
    args_non_interactive: bool, env: Mapping[str, str] | None = None
) -> bool:
    """Resolve whether this run should skip prompts.

    True when any of these holds:
        - `--non-interactive` (or `--yes`) was passed
        - `ASOF_NON_INTERACTIVE=1` env var is set
        - stdin is not a TTY (CI / pipeline runs)
    """
    env = env if env is not None else os.environ
    if args_non_interactive:
        return True
    if env.get("ASOF_NON_INTERACTIVE") == "1":
        return True
    # No stdin TTY → no human → behave non-interactively to avoid hangs.
    try:
        if not os.isatty(0):
            return True
    except (OSError, ValueError):
        pass
    return False


def prompt_yes_no(
    question: str,
    *,
    default: bool,
    input_fn=input,
    output_fn=print,
) -> bool:
    """Ask a yes/no question with a documented default.

    Empty input → default. Ctrl-D / Ctrl-C → returns the default (we don't
    treat aborts as separate from "use default" for yes/no — the layout
    prompt is where genuine abort handling happens).

    `input_fn` and `output_fn` are test seams.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        try:
            raw = input_fn(f"{question} {suffix} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return default
        if raw == "":
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        output_fn(f"  Please answer y or n (default: {'y' if default else 'n'}).")


def prompt_choice(
    question: str,
    choices: list[tuple[str, str]],
    *,
    default: str,
    input_fn=input,
    output_fn=print,
) -> str | object:
    """Ask the user to pick one of `choices` (a list of (key, description) tuples).

    Returns the chosen key, or `ABORTED` if the user aborts (Ctrl-D, Ctrl-C,
    or `q`). `default` is what gets returned on empty input.
    """
    output_fn(question)
    for key, description in choices:
        marker = " (default)" if key == default else ""
        output_fn(f"  [{key}] {description}{marker}")
    output_fn("  [q] quit")
    valid_keys = {k for k, _ in choices}
    while True:
        try:
            raw = input_fn("Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return ABORTED
        if raw == "":
            return default
        if raw == "q":
            return ABORTED
        if raw in valid_keys:
            return raw
        output_fn(f"  Invalid choice {raw!r}. Pick one of: {sorted(valid_keys)} or q.")


# ─── stage 2: layout choice ────────────────────────────────────────────────


def ask_layout(
    *,
    args_pattern: str | None,
    args_wiki_dir: str | None,
    args_source: str,  # always required from CLI (positional)
    args_non_interactive: bool,
    env: Mapping[str, str] | None = None,
    input_fn=input,
    output_fn=print,
) -> LayoutChoice | object:
    """Resolve the LayoutChoice from CLI args + (optionally) interactive input.

    Resolution rules:
        1. If `args_pattern` is provided (A / B / C), use it directly.
        2. Else if non-interactive mode, use DEFAULT_PATTERN.
        3. Else prompt the user to pick A / B / C.
        4. Then resolve wiki_dir per pattern (using --wiki-dir if provided,
           else interactive prompt with sane defaults).
        5. Resolve source per pattern (Pattern A/B: use args_source; Pattern C:
           args_source IS the repo, wiki lives at <repo>/.asof, source becomes
           None on the LayoutChoice — the .asof dir is derived from source).

    Returns LayoutChoice on success, or ABORTED if the user quit interactively.
    """
    pattern: Literal["A", "B", "C"]

    # Step 1-3: pattern
    if args_pattern:
        pattern = _validate_pattern(args_pattern)
    elif is_non_interactive(args_non_interactive, env):
        pattern = DEFAULT_PATTERN
    else:
        choice = prompt_choice(
            "Choose a wiki layout (PLAN.md section 4 explains each):",
            [
                ("a", "Shared wiki, multiple projects under ~/.claude/asof/ (recommended for solo users)"),
                ("b", "Per-project wiki under home (e.g. ~/.claude/asof-myproject/)"),
                ("c", "Wiki inside the source repo at <repo>/.asof/ (recommended for teams + open source)"),
            ],
            default="a",
            input_fn=input_fn,
            output_fn=output_fn,
        )
        if choice is ABORTED:
            return ABORTED
        pattern = choice.upper()  # type: ignore[assignment]

    # Step 4-5: wiki_dir + source per pattern
    source_path = Path(args_source).expanduser().resolve()
    if pattern == "A":
        wiki_dir = (
            Path(args_wiki_dir).expanduser().resolve()
            if args_wiki_dir
            else DEFAULT_WIKI_DIR.resolve()
        )
        return LayoutChoice(pattern="A", wiki_dir=wiki_dir, source=source_path)
    if pattern == "B":
        if args_wiki_dir:
            wiki_dir = Path(args_wiki_dir).expanduser().resolve()
        elif is_non_interactive(args_non_interactive, env):
            # Default for B in non-interactive: ~/.claude/asof-<source-name>/
            wiki_dir = (
                DEFAULT_WIKI_DIR.parent / f"asof-{source_path.name}"
            ).resolve()
        else:
            default_b = (
                DEFAULT_WIKI_DIR.parent / f"asof-{source_path.name}"
            ).resolve()
            try:
                raw = input_fn(
                    f"Wiki dir for Pattern B [{default_b}]: "
                ).strip()
            except (EOFError, KeyboardInterrupt):
                return ABORTED
            wiki_dir = (Path(raw).expanduser().resolve() if raw else default_b)
        return LayoutChoice(pattern="B", wiki_dir=wiki_dir, source=source_path)
    # Pattern C
    wiki_dir = (source_path / ".asof").resolve()
    return LayoutChoice(pattern="C", wiki_dir=wiki_dir, source=None)


def _validate_pattern(raw: str) -> Literal["A", "B", "C"]:
    upper = raw.strip().upper()
    if upper not in ("A", "B", "C"):
        raise ValueError(
            f"--pattern must be A, B, or C (got {raw!r})"
        )
    return upper  # type: ignore[return-value]


# ─── stage 5: integrations ─────────────────────────────────────────────────


def ask_integrations(
    *,
    layout: LayoutChoice,
    args_no_install_hook: bool,
    args_no_claudemd_snippet: bool,
    args_no_additional_directories: bool,
    args_skip_first_sync: bool,
    args_commit_settings: bool,
    args_non_interactive: bool,
    env: Mapping[str, str] | None = None,
    input_fn=input,
    output_fn=print,
) -> IntegrationChoice:
    """Resolve the four stage-5 yes/no choices.

    Each `args_no_*` flag is a CLI override that forces the corresponding
    choice to False (the user explicitly opted out). Otherwise we prompt
    interactively or use the defaults (all True except `commit_settings`).

    Pattern C overrides `add_additional_directories` to False because the
    wiki lives inside the source repo — no need to add it as an extra dir.
    """
    non_interactive = is_non_interactive(args_non_interactive, env)

    # CLAUDE.md snippet: default Yes; --no-claudemd-snippet forces No.
    if args_no_claudemd_snippet:
        install_claudemd_snippet = False
    elif non_interactive:
        install_claudemd_snippet = True
    else:
        install_claudemd_snippet = prompt_yes_no(
            "Append the wiki-precedence snippet to your project's CLAUDE.md?",
            default=True,
            input_fn=input_fn,
            output_fn=output_fn,
        )

    # Hook install: default Yes; --no-install-hook forces No.
    if args_no_install_hook:
        install_hook = False
    elif non_interactive:
        install_hook = True
    else:
        install_hook = prompt_yes_no(
            "Install the PostToolUse change-reminder hook in your project's "
            ".claude/?",
            default=True,
            input_fn=input_fn,
            output_fn=output_fn,
        )

    # Add additionalDirectories: default Yes for Pattern A/B; forced No for C.
    # --no-additional-directories also forces No.
    if layout.pattern == "C" or args_no_additional_directories:
        add_additional_directories = False
    elif non_interactive:
        add_additional_directories = True
    else:
        add_additional_directories = prompt_yes_no(
            f"Add {layout.wiki_dir} to your project's additionalDirectories so "
            "the agent can read the wiki?",
            default=True,
            input_fn=input_fn,
            output_fn=output_fn,
        )

    # First sync: default Yes; --skip-first-sync forces No.
    if args_skip_first_sync:
        run_first_sync = False
    elif non_interactive:
        run_first_sync = True
    else:
        run_first_sync = prompt_yes_no(
            "Run a first sync now to populate the wiki's raw/ mirror?",
            default=True,
            input_fn=input_fn,
            output_fn=output_fn,
        )

    return IntegrationChoice(
        install_claudemd_snippet=install_claudemd_snippet,
        install_hook=install_hook,
        add_additional_directories=add_additional_directories,
        run_first_sync=run_first_sync,
        commit_settings=args_commit_settings,
    )
