#!/usr/bin/env python3
"""asof:sync — entry point.

Run as `python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py [args]`.

Orchestrates the sync skill: resolves which wiki + project(s) to operate on,
applies version compatibility, runs rsync per project, detects deltas,
writes per-project last-sync reports, prints a human summary.

Modular design (PLAN.md section 6.2): each concern lives in its own file.
This entry point wires them together but holds no policy logic of its own.

Exit codes:
    0  success (or no deltas)
    1  rsync failure
    2  config / data-shape error
    3  version-compat refusal (cell a or b)
    4  project-selection failure (--project missing, cwd no match, etc.)
    5  user abort (interactive prompt declined)

Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import sys

from config import ConfigError, load_wiki_config
from delta import StrictMtimeError, detect_deltas
from report import render_human_report, render_run_summary, write_last_sync
from resolution import (
    CompatStatus,
    ProjectSelectionError,
    check_version_compat,
    resolve_projects,
    resolve_wiki_dir,
)
from rsync_runner import RsyncError, run_rsync
from utils import SKILL_VERSION, file_lock

# ─── exit codes ─────────────────────────────────────────────────────────────


class ExitCode:
    SUCCESS = 0
    RSYNC_ERROR = 1
    CONFIG_ERROR = 2
    COMPAT_REFUSED = 3
    PROJECT_SELECTION_ERROR = 4
    USER_ABORT = 5


# ─── CLI ────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asof:sync",
        description=(
            "Sync source repo .md files into the wiki's raw/ dir and "
            "detect what changed (NEW / MODIFIED / DELETED). Per-project "
            "scoping; cwd-aware project auto-select; concurrency-safe."
        ),
        epilog="See PLAN.md and references/INGEST_PROCEDURE.md for the full procedure.",
    )
    p.add_argument(
        "project", nargs="?", default=None,
        help="Project name to sync. Omit for cwd-aware auto-select or use --all.",
    )
    p.add_argument("--project", dest="project_flag", default=None,
                   help="Alias for the positional project arg.")
    p.add_argument("--wiki-dir", dest="wiki_dir", default=None,
                   help="Wiki dir override. Otherwise: $ASOF_DIR > walk-up > ~/.claude/asof.")
    p.add_argument("--all", action="store_true",
                   help="Sync every configured project.")
    p.add_argument("--dry-run", action="store_true",
                   help="Pass --dry-run to rsync and skip last-sync writes.")
    p.add_argument("--summary-only", action="store_true",
                   help="Suppress per-file delta listings; just print counts.")
    p.add_argument("--strict-mtime", action="store_true",
                   help="Fail if any recorded source_mtime is NEWER than current "
                        "(detects bookkeeping bugs).")
    p.add_argument("--non-interactive", "--yes", action="store_true",
                   dest="non_interactive",
                   help="Accept defaults / fail-fast on ambiguity. Required for CI.")
    p.add_argument("--auto-select-longest", action="store_true",
                   help="On nested-source multi-match in non-interactive mode, "
                        "deterministically pick the deepest match.")
    p.add_argument("--copy-links", action="store_true",
                   help="rsync follows symlinks instead of skipping (--safe-links default).")
    p.add_argument("--allow-self", action="store_true",
                   help="Bypass the self-ingest guard (Pattern C with proper excludes).")
    p.add_argument("--migrate", action="store_true",
                   help="Reserved: required when wiki_schema < skill_schema. "
                        "Currently rejects with 'not yet implemented' (v1.x).")
    p.add_argument("--version", action="version", version=f"asof:sync {SKILL_VERSION}")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Honor ASOF_NON_INTERACTIVE env even if --non-interactive isn't passed.
    non_interactive = args.non_interactive or os.environ.get(
        "ASOF_NON_INTERACTIVE"
    ) == "1"

    # `project` (positional) and `--project` are aliases. Last wins.
    project_name = args.project_flag or args.project

    # ── resolve wiki + load config ──────────────────────────────────────────
    try:
        wiki_dir = resolve_wiki_dir(args.wiki_dir)
        config = load_wiki_config(wiki_dir)
    except FileNotFoundError as exc:
        print(f"asof:sync: {exc}", file=sys.stderr)
        return ExitCode.CONFIG_ERROR
    except ConfigError as exc:
        print(f"asof:sync: config error — {exc}", file=sys.stderr)
        return ExitCode.CONFIG_ERROR

    # ── version compat check (the four-cell matrix) ────────────────────────
    compat = check_version_compat(config)
    if compat.status == CompatStatus.REFUSE:
        print(f"asof:sync: {compat.message}", file=sys.stderr)
        return ExitCode.COMPAT_REFUSED
    if compat.status == CompatStatus.READ_ONLY:
        print(
            f"asof:sync: read-only mode — sync rejected. {compat.message}",
            file=sys.stderr,
        )
        return ExitCode.COMPAT_REFUSED
    if compat.status == CompatStatus.REQUIRE_MIGRATE and not args.migrate:
        print(f"asof:sync: {compat.message}", file=sys.stderr)
        return ExitCode.COMPAT_REFUSED
    if args.migrate:
        # Migration support is reserved for v1.x; for now, refuse explicitly.
        print(
            "asof:sync: --migrate is not yet implemented (reserved for v1.x). "
            "Wiki migration scripts will land alongside the first breaking "
            "schema change. For now, downgrade the skill or upgrade the wiki "
            "manually.",
            file=sys.stderr,
        )
        return ExitCode.CONFIG_ERROR

    # ── project selection ──────────────────────────────────────────────────
    try:
        projects = resolve_projects(
            config,
            name=project_name,
            all_projects=args.all,
            non_interactive=non_interactive,
            auto_select_longest=args.auto_select_longest,
        )
    except ProjectSelectionError as exc:
        print(f"asof:sync: {exc}", file=sys.stderr)
        return ExitCode.PROJECT_SELECTION_ERROR
    except ValueError as exc:  # slugify rejection from resolve_projects
        print(f"asof:sync: {exc}", file=sys.stderr)
        return ExitCode.PROJECT_SELECTION_ERROR

    # Multi-match interactive: ask the user which to sync.
    if len(projects) > 1 and not args.all and not args.auto_select_longest:
        if non_interactive:
            # resolve_projects should have already raised; defensive belt.
            print(
                "asof:sync: ambiguous project selection in non-interactive mode",
                file=sys.stderr,
            )
            return ExitCode.PROJECT_SELECTION_ERROR
        chosen = _prompt_project_choice(projects)
        if chosen is None:
            return ExitCode.USER_ABORT
        projects = [chosen]

    # ── run sync per project (under the wiki lock) ─────────────────────────
    reports: list = []
    rsync_failed = False
    with file_lock(config.lock_path):
        for proj in projects:
            try:
                rsync_result = run_rsync(
                    proj,
                    config.wiki_dir,
                    follow_symlinks=args.copy_links,
                    dry_run=args.dry_run,
                    allow_self=args.allow_self,
                )
            except RsyncError as exc:
                print(f"asof:sync: {exc}", file=sys.stderr)
                rsync_failed = True
                continue
            except ConfigError as exc:
                print(f"asof:sync: {exc}", file=sys.stderr)
                return ExitCode.CONFIG_ERROR

            try:
                delta = detect_deltas(
                    proj,
                    config.wiki_dir,
                    follow_symlinks=args.copy_links,
                    strict_mtime=args.strict_mtime,
                )
            except StrictMtimeError as exc:
                print(f"asof:sync: strict-mtime check failed — {exc}", file=sys.stderr)
                return ExitCode.CONFIG_ERROR

            if not args.dry_run:
                write_last_sync(config, delta, rsync_result)

            print(
                render_human_report(
                    delta, rsync_result, summary_only=args.summary_only
                )
            )
            reports.append((delta, rsync_result))

    # ── final summary ─────────────────────────────────────────────────────
    if reports:
        print(render_run_summary(reports))

    return ExitCode.RSYNC_ERROR if rsync_failed else ExitCode.SUCCESS


# ─── interactive prompt helper ─────────────────────────────────────────────


def _prompt_project_choice(projects: list) -> object:
    """Prompt the user to pick one of multiple matched projects.

    Returns the chosen ProjectConfig, or None if the user declined (Ctrl-D / empty).
    Kept tiny + isolated so non-interactive paths can skip it cleanly.
    """
    print("Multiple projects match the current directory:")
    for i, p in enumerate(projects, 1):
        print(f"  [{i}] {p.name}  (source: {p.source})")
    print("  [a] all")
    print("  [q] quit")
    while True:
        try:
            choice = input("Pick a project [1-N / a / q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice == "q" or not choice:
            return None
        if choice == "a":
            # Caller pivots to all-projects mode by interpreting len > 1.
            return projects[0]  # caller should re-detect; out of scope for v1
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(projects):
                return projects[idx]
        print(f"  invalid choice: {choice!r}")


if __name__ == "__main__":
    sys.exit(main())
