#!/usr/bin/env python3
"""asof:lint — entry point.

Run as: python3 ${CLAUDE_SKILL_DIR}/scripts/lint.py [args]

Per PLAN.md §6.3:
    1. Pre-flight config gate via load_wiki_config (halts on invalid config).
    2. Acquire <wiki_dir>/.asof.lock (same lock as sync).
    3. Walk every project's wiki/<project>/ collecting parsed pages.
    4. Run the 7 checks per project.
    5. Render text or JSON; optionally apply --fix.
    6. Exit with code mapped from (filtered) findings + read-only state.

Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from _lint_bridge import (
    SKILL_VERSION,
    CompatStatus,
    ConfigError,
    ProjectConfig,
    WikiConfig,
    check_version_compat,
    file_lock,
    load_wiki_config,
)
from checks import ProjectContext, run_all_checks
from fix import FixResult, apply_fixes
from frontmatter import parse_page
from model import Finding, LintReport, ParsedPage, ProjectReport, Severity
from render import compute_exit_code, render_json, render_text


class ExitCode:
    SUCCESS = 0
    FINDINGS = 1
    INTERNAL_ERROR = 2
    FIX_FAILED = 3
    PRECONDITION = 4  # bad config / read-only --fix / unknown project


# ─── CLI ───────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asof:lint",
        description=(
            "Audit the asof wiki for schema violations. Pre-flight checks "
            ".asof.json validity, then runs 7 page-level checks (frontmatter, "
            "path-mismatch, missing-mtime, removed-source, mtime-drift, "
            "supersession-gap, orphan-page) per project."
        ),
        epilog="See PLAN.md §6.3 for the full design.",
    )
    p.add_argument(
        "project_name",
        nargs="?",
        default=None,
        help="Lint only the named project. Omit to lint all configured projects.",
    )
    p.add_argument(
        "--wiki-dir",
        default=None,
        help="Override the wiki dir resolution (else looks in standard locations).",
    )
    p.add_argument(
        "--fix",
        action="store_true",
        help="Apply the 2 narrow auto-fixes (insert missing last_updated; "
        "append orphan entries to index.md). Rejected in read-only mode.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human-readable text.",
    )
    p.add_argument(
        "--severity",
        choices=["error", "warn", "info"],
        default="info",
        help="Filter findings to this severity and above (default: info).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fix, report what would be fixed without writing.",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="No prompts (auto-detected from non-TTY stdin).",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"asof:lint {SKILL_VERSION}",
    )
    return p


# ─── wiki-dir resolution ──────────────────────────────────────────────────


def _resolve_wiki_dir(explicit: str | None) -> Path:
    """Pick the wiki dir to lint.

    Order: --wiki-dir flag → cwd's enclosing .asof/ (Pattern C) → default
    Pattern A path (~/.claude/asof/). On unresolvable, raises FileNotFoundError.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    cwd = Path.cwd().resolve()
    # Pattern C: walk up looking for an `.asof` directory with a `.asof.json`.
    cur = cwd
    while True:
        candidate = cur / ".asof"
        if (candidate / ".asof.json").is_file():
            return candidate.resolve()
        if cur.parent == cur:
            break
        cur = cur.parent
    # Pattern A default.
    default = (Path.home() / ".claude" / "asof").resolve()
    if (default / ".asof.json").is_file():
        return default
    raise FileNotFoundError(
        "asof:lint: cannot resolve wiki dir. Pass --wiki-dir <path>, or run "
        "from inside a Pattern C repo, or ensure ~/.claude/asof/.asof.json exists."
    )


# ─── page collection ──────────────────────────────────────────────────────


def _collect_pages(
    project: ProjectConfig, wiki_dir: Path
) -> tuple[list[ParsedPage], str | None]:
    """Walk wiki/<project>/ collecting every *.md page.

    Returns (pages, project_level_error). When project_level_error is set,
    pages is empty (the caller surfaces the error and skips checks).
    """
    project_dir = (wiki_dir / project.wiki_subdir).resolve()
    if not project_dir.is_dir():
        return ([], f"project's wiki dir {project_dir!s} does not exist")
    pages: list[ParsedPage] = []
    for path in sorted(project_dir.rglob("*.md")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            # Treat unreadable pages as 0-frontmatter pages so the
            # frontmatter check fires rather than aborting the whole project.
            text = ""
            _ = exc  # documented intent; no special handling needed
        fm, body, _ = parse_page(text)
        relative = path.resolve().relative_to(wiki_dir.resolve()).as_posix()
        project_relative = path.resolve().relative_to(project_dir).as_posix()
        pages.append(
            ParsedPage(
                absolute_path=path,
                relative_path=relative,
                project_relative_path=project_relative,
                frontmatter=fm,
                body=body,
                raw_text=text,
            )
        )
    return (pages, None)


def _build_project_context(
    project: ProjectConfig, wiki_cfg: WikiConfig, today: datetime.date
) -> ProjectContext:
    """Assemble the per-project context for checks.

    Pulls thresholds from `wiki_cfg.lint_thresholds` (which load_wiki_config
    fills with defaults when the user's `.asof.json` doesn't override them).
    """
    return ProjectContext(
        project_name=project.name,
        wiki_dir=wiki_cfg.wiki_dir,
        project_dir=(wiki_cfg.wiki_dir / project.wiki_subdir).resolve(),
        raw_project_dir=(wiki_cfg.wiki_dir / project.raw_subdir).resolve(),
        mtime_drift_days=wiki_cfg.lint_thresholds.get("mtime_drift_days", 30),
        supersession_gap_days=wiki_cfg.lint_thresholds.get(
            "supersession_gap_days", 60
        ),
        today=today,
    )


# ─── main flow ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # --- 1. Resolve wiki dir + load config (pre-flight gate) -----------------
    try:
        wiki_dir = _resolve_wiki_dir(args.wiki_dir)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return ExitCode.PRECONDITION

    try:
        wiki_cfg = load_wiki_config(wiki_dir)
    except ConfigError as exc:
        print(
            f"asof:lint: invalid config at {wiki_dir / '.asof.json'}: {exc}",
            file=sys.stderr,
        )
        print(
            "Lint cannot run on an untrusted config. Fix it by hand, or "
            "delete and re-run /asof:init.",
            file=sys.stderr,
        )
        return ExitCode.PRECONDITION
    except FileNotFoundError as exc:
        print(f"asof:lint: {exc}", file=sys.stderr)
        return ExitCode.PRECONDITION

    # --- 2. Read-only check (compat-matrix cell b) --------------------------
    compat = check_version_compat(wiki_cfg)
    if args.fix and compat.status == CompatStatus.READ_ONLY:
        print(
            f"asof:lint: read-only mode — skill version {SKILL_VERSION} can "
            f"read this wiki but not write it. Upgrade asof to "
            f">= {wiki_cfg.min_writer_version} to use --fix, or re-run "
            "without --fix for a report-only audit.",
            file=sys.stderr,
        )
        return ExitCode.PRECONDITION
    if compat.status == CompatStatus.REFUSE:
        print(
            f"asof:lint: incompatible — {compat.message}",
            file=sys.stderr,
        )
        return ExitCode.PRECONDITION

    # --- 3. Project selection ------------------------------------------------
    selected_projects = _select_projects(wiki_cfg, args.project_name)
    if isinstance(selected_projects, int):
        return selected_projects  # exit code from helper

    # --- 4. Acquire lock + iterate projects ---------------------------------
    severity_filter = Severity.from_string(args.severity)
    today = datetime.date.today()
    lock_path = wiki_cfg.wiki_dir / ".asof.lock"
    try:
        with file_lock(lock_path):
            project_reports = _lint_projects(
                selected_projects, wiki_cfg, today
            )
    except (OSError, RuntimeError) as exc:
        print(f"asof:lint: lock acquisition failed: {exc}", file=sys.stderr)
        return ExitCode.INTERNAL_ERROR

    # --- 5. Optional --fix --------------------------------------------------
    fix_result: FixResult | None = None
    if args.fix:
        fix_result = _apply_lint_fixes(
            project_reports, selected_projects, wiki_cfg, today, dry_run=args.dry_run
        )

    # --- 6. Render -----------------------------------------------------------
    report = LintReport(
        wiki_dir=wiki_cfg.wiki_dir,
        skill_version=SKILL_VERSION,
        projects=tuple(project_reports),
    )
    if args.json:
        sys.stdout.write(render_json(report, severity_filter))
    else:
        print(render_text(report, severity_filter))
        if fix_result is not None:
            _print_fix_summary(fix_result, dry_run=args.dry_run)

    # --- 7. Exit code -------------------------------------------------------
    if any(p.project_level_error for p in project_reports):
        return ExitCode.PRECONDITION
    if fix_result is not None and fix_result.refused:
        # `--fix` was asked but at least one fixable finding was refused —
        # exit 3 per PLAN. The applied ones still write through.
        return ExitCode.FIX_FAILED
    return compute_exit_code(report, severity_filter)


# ─── helpers used by main() ────────────────────────────────────────────────


def _select_projects(
    wiki_cfg: WikiConfig, requested: str | None
) -> list[ProjectConfig] | int:
    """Pick projects to lint. Returns either a list or an exit code."""
    if requested is None:
        return list(wiki_cfg.projects)
    match = next((p for p in wiki_cfg.projects if p.name == requested), None)
    if match is None:
        valid = ", ".join(p.name for p in wiki_cfg.projects) or "(no projects configured)"
        print(
            f"asof:lint: unknown project {requested!r}. Valid: {valid}",
            file=sys.stderr,
        )
        return ExitCode.PRECONDITION
    return [match]


def _lint_projects(
    projects: list[ProjectConfig], wiki_cfg: WikiConfig, today: datetime.date
) -> list[ProjectReport]:
    """Run all checks for each project; return per-project reports."""
    reports: list[ProjectReport] = []
    for project in projects:
        ctx = _build_project_context(project, wiki_cfg, today)
        pages, error = _collect_pages(project, wiki_cfg.wiki_dir)
        if error:
            reports.append(
                ProjectReport(
                    project_name=project.name,
                    page_count=0,
                    findings=(),
                    project_level_error=error,
                )
            )
            continue
        findings = run_all_checks(pages, ctx)
        reports.append(
            ProjectReport(
                project_name=project.name,
                page_count=len(pages),
                findings=tuple(findings),
            )
        )
    return reports


def _apply_lint_fixes(
    project_reports: list[ProjectReport],
    selected_projects: list[ProjectConfig],
    wiki_cfg: WikiConfig,
    today: datetime.date,
    *,
    dry_run: bool,
) -> FixResult:
    """Build the page lookup + project_dirs maps and dispatch to apply_fixes."""
    pages_by_relpath: dict[str, ParsedPage] = {}
    project_dirs: dict[str, Path] = {}
    all_findings: list[Finding] = []
    for project in selected_projects:
        project_dirs[project.name] = (
            wiki_cfg.wiki_dir / project.wiki_subdir
        ).resolve()
        pages, _ = _collect_pages(project, wiki_cfg.wiki_dir)
        for page in pages:
            pages_by_relpath[page.relative_path] = page
    for report in project_reports:
        all_findings.extend(report.findings)
    return apply_fixes(
        all_findings,
        pages_by_relpath,
        project_dirs,
        today,
        dry_run=dry_run,
    )


def _print_fix_summary(result: FixResult, *, dry_run: bool) -> None:
    if not result.applied and not result.refused:
        return
    print()
    if dry_run:
        print("--fix --dry-run: would apply the following:")
    else:
        print("--fix applied:")
    for f in result.applied:
        print(f"  ✓ {f.page}: {f.check} ({f.message[:60]}...)")
    for f, reason in result.refused:
        print(f"  · refused {f.page}: {reason}")


if __name__ == "__main__":
    sys.exit(main())
