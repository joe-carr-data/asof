"""Render a LintReport as human-readable text or JSON.

Two functions: `render_text(report, severity_filter)` produces the default
output shape from PLAN.md §6.3; `render_json(report, severity_filter)`
produces the machine-readable shape for CI pipelines.

Both honour the `severity_filter` (only emit findings ≥ filter severity).

Stdlib only.
"""

from __future__ import annotations

import json

from model import Finding, LintReport, Severity


def filter_by_severity(
    findings: tuple[Finding, ...], threshold: Severity
) -> list[Finding]:
    """Return findings with severity >= threshold.

    Severity ordering: ERROR (3) > WARN (2) > INFO (1).
    Default threshold is INFO so everything is reported.
    """
    return [f for f in findings if f.severity.value >= threshold.value]


# ─── text renderer ─────────────────────────────────────────────────────────


def render_text(
    report: LintReport, severity_filter: Severity = Severity.INFO
) -> str:
    """Render the human-readable text shape from PLAN.md §6.3.

    Layout:
        asof:lint <wiki_dir>  (project: <slug> | all projects)

        ERRORS (N):
          <relative-page>:<line>  <check-id>  <message>
          ...

        WARNINGS (N):
          ...

        INFO (N):
          ...

        Summary: <e> errors, <w> warnings, <i> info across <p> pages.
    """
    lines: list[str] = []
    project_label = (
        f"project: {report.projects[0].project_name}"
        if len(report.projects) == 1
        else f"all {len(report.projects)} projects"
    )
    lines.append(f"asof:lint {report.wiki_dir}  ({project_label})")
    lines.append("")

    # Project-level errors first (one entry per project that hard-failed).
    for project in report.projects:
        if project.project_level_error:
            lines.append(
                f"PROJECT ERROR ({project.project_name}): "
                f"{project.project_level_error}"
            )
            lines.append("")

    all_findings = filter_by_severity(report.all_findings, severity_filter)
    by_severity: dict[Severity, list[Finding]] = {
        Severity.ERROR: [],
        Severity.WARN: [],
        Severity.INFO: [],
    }
    for f in all_findings:
        by_severity[f.severity].append(f)

    section_titles = (
        (Severity.ERROR, "ERRORS"),
        (Severity.WARN, "WARNINGS"),
        (Severity.INFO, "INFO"),
    )
    for sev, title in section_titles:
        items = by_severity[sev]
        if not items:
            continue
        lines.append(f"{title} ({len(items)}):")
        # Sort within section: page asc, line asc, check asc — stable readout.
        for f in sorted(items, key=lambda x: (x.page, x.line or 0, x.check)):
            location = f"{f.page}:{f.line}" if f.line is not None else f.page
            lines.append(f"  {location:<46} {f.check:<18} {f.message}")
        lines.append("")

    counts = report.summary_counts()
    total_pages = sum(p.page_count for p in report.projects)
    lines.append(
        f"Summary: {counts['errors']} errors, {counts['warnings']} warnings, "
        f"{counts['info']} info across {total_pages} pages."
    )
    if not all_findings and not any(p.project_level_error for p in report.projects):
        lines.append("Wiki is clean.")
    return "\n".join(lines)


# ─── JSON renderer ─────────────────────────────────────────────────────────


def render_json(
    report: LintReport, severity_filter: Severity = Severity.INFO
) -> str:
    """Render the machine-readable JSON shape from PLAN.md §6.3.

    Top-level keys: wiki_dir, skill_version, projects[], summary{}.
    Each project entry has: name, page_count, project_level_error, findings[].
    Each finding: severity (string), check, page, line, message.
    """
    payload: dict = {
        "wiki_dir": str(report.wiki_dir),
        "skill_version": report.skill_version,
        "projects": [],
        "summary": report.summary_counts(),
    }
    for project in report.projects:
        filtered = filter_by_severity(project.findings, severity_filter)
        payload["projects"].append(
            {
                "name": project.project_name,
                "page_count": project.page_count,
                "project_level_error": project.project_level_error,
                "findings": [
                    {
                        "severity": f.severity.name,
                        "check": f.check,
                        "page": f.page,
                        "line": f.line,
                        "message": f.message,
                    }
                    for f in sorted(
                        filtered,
                        key=lambda x: (-x.severity.value, x.page, x.line or 0, x.check),
                    )
                ],
            }
        )
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


# ─── exit-code logic ───────────────────────────────────────────────────────


def compute_exit_code(
    report: LintReport, severity_filter: Severity
) -> int:
    """Map a (filtered) report to the lint exit code per PLAN.md §6.3.

    0 — clean (no findings at or above filter threshold).
    1 — findings present (ERROR or WARN at any threshold; INFO only when
        the user explicitly asked for `--severity info`).
    """
    filtered = filter_by_severity(report.all_findings, severity_filter)
    if not filtered:
        return 0
    return 1
