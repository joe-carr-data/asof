#!/usr/bin/env python3
"""asof:init — entry point.

Run as: python3 ${CLAUDE_SKILL_DIR}/scripts/init.py [args]

Orchestrates the 5-stage wizard:
    Stage 1 — preflight (Python / rsync / git / Obsidian checks)
    Stage 2 — pick wiki layout (A / B / C)
    Stage 3 — create wiki dir + CLAUDE.md + .asof.json
    Stage 4 — scaffold project pages from templates
    Stage 5 — integrations (CLAUDE.md snippet, hook, settings, first sync)

Each stage is implemented in its own module (preflight, wizard, scaffold,
integrations). init.py holds CLI parsing + orchestration + the user-facing
summary. Zero policy logic of its own.

Exit codes:
    0  success
    1  user aborted at a prompt
    2  preflight failure (required dependency missing)
    3  scaffold error (template substitution / config write / etc.)
    4  integration error (settings JSON malformed, hook copy failed, etc.)
    5  --import-existing not yet implemented (v1 stub)

Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime
import sys

from _sync_bridge import SKILL_VERSION, slugify
from integrations import (
    IntegrationRequest,
    IntegrationResult,
    apply_integrations,
)
from preflight import render_preflight, run_preflight
from scaffold import (
    ScaffoldError,
    ScaffoldRequest,
    ScaffoldResult,
    do_scaffold,
)
from wizard import (
    ABORTED,
    LayoutChoice,
    ask_integrations,
    ask_layout,
)

# ─── exit codes ────────────────────────────────────────────────────────────


class ExitCode:
    SUCCESS = 0
    USER_ABORT = 1
    PREFLIGHT_FAILED = 2
    SCAFFOLD_ERROR = 3
    INTEGRATION_ERROR = 4
    NOT_IMPLEMENTED = 5


# ─── CLI ───────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asof:init",
        description=(
            "Bootstrap a time-aware asof wiki for a project. Five-stage "
            "interactive wizard: preflight checks, wiki layout choice, "
            "wiki dir + config creation, project page scaffold, "
            "integrations (CLAUDE.md snippet, hook, settings, first sync)."
        ),
        epilog="See PLAN.md section 6.1 for the full design.",
    )
    p.add_argument(
        "project_name",
        nargs="?",
        default=None,
        help="Project display name (slugified for paths).",
    )
    p.add_argument(
        "source_path",
        nargs="?",
        default=None,
        help="Path to the source repo. For Pattern C, this is the repo "
        "root (.asof/ is created inside it). For Pattern A/B, this is "
        "the dir whose .md files will be mirrored into the wiki's raw/.",
    )
    p.add_argument(
        "--pattern",
        choices=["A", "B", "C", "a", "b", "c"],
        default=None,
        help="Wiki layout pattern: A (shared, default), B (per-project under "
        "home), C (in-repo at <source>/.asof/).",
    )
    p.add_argument(
        "--wiki-dir",
        default=None,
        help="Override the default wiki dir path. Ignored for Pattern C "
        "(wiki dir is always <source>/.asof/).",
    )
    p.add_argument(
        "--non-interactive",
        "--yes",
        dest="non_interactive",
        action="store_true",
        help="Accept defaults / fail-fast on ambiguity. Required for CI.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write to the filesystem; report what would happen.",
    )
    p.add_argument(
        "--no-install-hook",
        action="store_true",
        help="Skip installing the PostToolUse change-reminder hook.",
    )
    p.add_argument(
        "--no-claudemd-snippet",
        action="store_true",
        help="Skip appending the wiki-precedence snippet to the project's "
        "CLAUDE.md.",
    )
    p.add_argument(
        "--no-additional-directories",
        action="store_true",
        help="Skip adding the wiki dir to .claude/settings*.json's "
        "permissions.additionalDirectories.",
    )
    p.add_argument(
        "--skip-first-sync",
        action="store_true",
        help="Don't run an initial asof:sync after init completes.",
    )
    p.add_argument(
        "--commit-settings",
        action="store_true",
        help="Write to .claude/settings.json (committed) instead of "
        ".claude/settings.local.json (gitignored). Use only when the "
        "absolute paths in the settings are project-portable (e.g. all "
        "developers use the same $HOME).",
    )
    p.add_argument(
        "--import-existing",
        default=None,
        help="(v1.x) Import an existing brain-sync layout. Currently stubbed; "
        "see PLAN.md section 13 for the migration runbook.",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"asof:init {SKILL_VERSION}",
    )
    return p


# ─── main flow ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # --import-existing: v1 stub. Print a clear error + pointer.
    if args.import_existing:
        print(
            "asof:init: --import-existing is not yet implemented (v1 stub). "
            "See PLAN.md section 13 for the manual migration runbook from "
            "the brain-sync prototype to asof. Re-run init without "
            "--import-existing to bootstrap a fresh wiki.",
            file=sys.stderr,
        )
        return ExitCode.NOT_IMPLEMENTED

    # Required positional args (project_name + source_path) — argparse keeps
    # them as nargs='?' so we can give a clearer error than argparse's default.
    if not args.project_name or not args.source_path:
        parser.print_usage(sys.stderr)
        print(
            "\nasof:init: project_name and source_path are required.\n"
            "  Example: asof:init myproject /path/to/repo\n"
            "  See `asof:init --help` for all flags.",
            file=sys.stderr,
        )
        return ExitCode.SCAFFOLD_ERROR

    # Slugify upstream so all downstream code uses a validated slug.
    try:
        project_slug = slugify(args.project_name)
    except ValueError as exc:
        print(
            f"asof:init: invalid project_name {args.project_name!r}: {exc}",
            file=sys.stderr,
        )
        return ExitCode.SCAFFOLD_ERROR

    today = datetime.date.today().isoformat()

    # ─── Stage 1: preflight ────────────────────────────────────────────────
    preflight_report = run_preflight()
    print(render_preflight(preflight_report))
    if preflight_report.has_required_failure:
        print(
            "\nasof:init: required dependencies missing — see notes above. "
            "Aborting.",
            file=sys.stderr,
        )
        return ExitCode.PREFLIGHT_FAILED

    # ─── Stage 2: layout choice ───────────────────────────────────────────
    print("\n=== Stage 2: wiki layout ===")
    layout_or_aborted = ask_layout(
        args_pattern=args.pattern,
        args_wiki_dir=args.wiki_dir,
        args_source=args.source_path,
        args_non_interactive=args.non_interactive,
    )
    if layout_or_aborted is ABORTED:
        print("asof:init: user aborted at layout choice.", file=sys.stderr)
        return ExitCode.USER_ABORT
    layout: LayoutChoice = layout_or_aborted  # type: ignore[assignment]
    print(f"  pattern: {layout.pattern}")
    print(f"  wiki dir: {layout.wiki_dir}")
    if layout.source is not None:
        print(f"  source: {layout.source}")
    else:
        print("  source: (auto-derived as wiki_dir.parent — Pattern C)")

    # ─── Stages 3 + 4: scaffold ───────────────────────────────────────────
    print("\n=== Stages 3 + 4: scaffold wiki + project pages ===")
    request = ScaffoldRequest(
        layout=layout,
        project_display_name=args.project_name,
        project_slug=project_slug,
    )
    try:
        scaffold_result = do_scaffold(
            request, today=today, dry_run=args.dry_run
        )
    except ScaffoldError as exc:
        print(f"asof:init: scaffold error — {exc}", file=sys.stderr)
        return ExitCode.SCAFFOLD_ERROR
    _print_scaffold_summary(scaffold_result)

    # ─── Stage 5: integrations ────────────────────────────────────────────
    print("\n=== Stage 5: integrations ===")
    # Pattern A/B: project_root = source. Pattern C: project_root = wiki_dir.parent.
    project_root = (
        layout.source if layout.source is not None else layout.wiki_dir.parent
    )
    integration_choices = ask_integrations(
        layout=layout,
        args_no_install_hook=args.no_install_hook,
        args_no_claudemd_snippet=args.no_claudemd_snippet,
        args_no_additional_directories=args.no_additional_directories,
        args_skip_first_sync=args.skip_first_sync,
        args_commit_settings=args.commit_settings,
        args_non_interactive=args.non_interactive,
    )
    integration_request = IntegrationRequest(
        layout=layout,
        project_slug=project_slug,
        project_display_name=args.project_name,
        project_root=project_root,
        choices=integration_choices,
    )
    try:
        integration_result = apply_integrations(
            integration_request, today=today, dry_run=args.dry_run
        )
    except RuntimeError as exc:
        print(f"asof:init: integration error — {exc}", file=sys.stderr)
        return ExitCode.INTEGRATION_ERROR
    _print_integration_summary(integration_result)

    # ─── Final summary + next steps ───────────────────────────────────────
    _print_final_summary(layout, project_slug, integration_result, args.dry_run)
    return ExitCode.SUCCESS


# ─── output helpers ────────────────────────────────────────────────────────


def _print_scaffold_summary(result: ScaffoldResult) -> None:
    if result.dry_run:
        print("  (dry-run; no files written)")
    if result.wiki_dir_created:
        print("  ✓ wiki dir created")
    for p in result.files_created:
        print(f"  ✓ created  {p}")
    for p in result.files_updated:
        print(f"  ⟳ updated  {p}")
    for p in result.files_skipped:
        print(f"  · skipped  {p} (already exists)")


def _print_integration_summary(result: IntegrationResult) -> None:
    if result.dry_run:
        print("  (dry-run; no files written)")
    if result.snippet_appended:
        print("  ✓ wiki-precedence snippet appended to project CLAUDE.md")
    elif result.snippet_skipped_already_present:
        print("  · CLAUDE.md snippet skipped (marker already present)")
    if result.hook_installed:
        print("  ✓ PostToolUse change-reminder hook installed")
    elif result.hook_skipped_already_present:
        print("  · hook skipped (already installed)")
    if result.settings_path:
        suffix = " (committed)" if result.settings_path.name == "settings.json" else ""
        print(f"  ✓ settings file updated: {result.settings_path}{suffix}")
        if result.additional_dir_added:
            print("    + wiki_dir added to permissions.additionalDirectories")
        elif result.additional_dir_already_present:
            print("    · wiki_dir already in additionalDirectories (skipped)")
    if result.first_sync_ran:
        glyph = "✓" if result.first_sync_exit_code == 0 else "✗"
        print(f"  {glyph} first sync exit code: {result.first_sync_exit_code}")


def _print_final_summary(
    layout: LayoutChoice,
    project_slug: str,
    integration_result: IntegrationResult,
    dry_run: bool,
) -> None:
    print("\n=== asof:init complete ===")
    if dry_run:
        print("  (dry-run mode — re-run without --dry-run to actually create)")
        return
    print(f"\nWiki:    {layout.wiki_dir}")
    print(f"Project: {project_slug} ({layout.pattern})")
    print("\nNext steps:")
    print(f"  • Browse the wiki:  open {layout.wiki_dir}")
    if not integration_result.first_sync_ran:
        print(
            f"  • Run first sync:    /asof:sync {project_slug}"
        )
    elif integration_result.first_sync_exit_code != 0:
        print(
            "  • First sync failed — check the output above; you may need "
            "to re-run /asof:sync after fixing the issue."
        )
    print(
        "  • The agent will now read the wiki when answering project "
        "questions (per the snippet appended to CLAUDE.md)."
    )


if __name__ == "__main__":
    sys.exit(main())
