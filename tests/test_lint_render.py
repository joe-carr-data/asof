"""Tests for skills/lint/scripts/render.py — text + JSON output, exit codes,
severity filtering."""

from __future__ import annotations

import json
from pathlib import Path

from model import Finding, LintReport, ProjectReport, Severity
from render import compute_exit_code, filter_by_severity, render_json, render_text


def _build_report(*, findings: list[Finding], wiki_dir: Path) -> LintReport:
    return LintReport(
        wiki_dir=wiki_dir,
        skill_version="1.2.3",
        projects=(
            ProjectReport(
                project_name="myproj",
                page_count=10,
                findings=tuple(findings),
            ),
        ),
    )


def _err(page: str = "wiki/myproj/x.md", line: int | None = 1) -> Finding:
    return Finding(
        severity=Severity.ERROR,
        check="frontmatter",
        page=page,
        line=line,
        message="missing required field 'title'",
    )


def _warn() -> Finding:
    return Finding(
        severity=Severity.WARN,
        check="mtime-drift",
        page="wiki/myproj/y.md",
        line=5,
        message="last_updated 2026-01-01 is 90d older than newest source_mtime",
    )


def _info() -> Finding:
    return Finding(
        severity=Severity.INFO,
        check="orphan-page",
        page="wiki/myproj/z.md",
        line=None,
        message="no inbound links",
    )


# ─── filter_by_severity ────────────────────────────────────────────────────


def test_filter_includes_threshold_and_above() -> None:
    findings = (_err(), _warn(), _info())
    assert len(filter_by_severity(findings, Severity.WARN)) == 2
    assert len(filter_by_severity(findings, Severity.ERROR)) == 1
    assert len(filter_by_severity(findings, Severity.INFO)) == 3


# ─── render_text ───────────────────────────────────────────────────────────


def test_render_text_clean_report(tmp_path: Path) -> None:
    out = render_text(_build_report(findings=[], wiki_dir=tmp_path))
    assert "Wiki is clean." in out
    assert "0 errors" in out


def test_render_text_groups_by_severity(tmp_path: Path) -> None:
    out = render_text(
        _build_report(findings=[_err(), _warn(), _info()], wiki_dir=tmp_path)
    )
    err_idx = out.index("ERRORS (1)")
    warn_idx = out.index("WARNINGS (1)")
    info_idx = out.index("INFO (1)")
    # Sections appear in this exact order.
    assert err_idx < warn_idx < info_idx


def test_render_text_omits_empty_sections(tmp_path: Path) -> None:
    """Sections with zero findings are not printed."""
    out = render_text(_build_report(findings=[_err()], wiki_dir=tmp_path))
    assert "ERRORS (1)" in out
    assert "WARNINGS" not in out
    assert "INFO" not in out  # also not present in the summary line text? check
    # Summary line still includes "0 info" but no "INFO (n):" section header.


def test_render_text_severity_filter(tmp_path: Path) -> None:
    """--severity warn drops INFO from the body."""
    out = render_text(
        _build_report(findings=[_err(), _warn(), _info()], wiki_dir=tmp_path),
        severity_filter=Severity.WARN,
    )
    assert "ERRORS (1)" in out
    assert "WARNINGS (1)" in out
    assert "INFO (1)" not in out  # filtered out


def test_render_text_includes_line_when_present(tmp_path: Path) -> None:
    out = render_text(
        _build_report(findings=[_err(line=42)], wiki_dir=tmp_path)
    )
    assert "wiki/myproj/x.md:42" in out


def test_render_text_omits_line_when_none(tmp_path: Path) -> None:
    """Whole-page findings (orphan-page) print without `:line` suffix."""
    out = render_text(_build_report(findings=[_info()], wiki_dir=tmp_path))
    assert "wiki/myproj/z.md " in out  # space after path, no colon
    assert "wiki/myproj/z.md:" not in out


def test_render_text_project_level_error(tmp_path: Path) -> None:
    """A project with project_level_error gets its own line at the top."""
    report = LintReport(
        wiki_dir=tmp_path,
        skill_version="1.0.0",
        projects=(
            ProjectReport(
                project_name="broken",
                page_count=0,
                findings=(),
                project_level_error="wiki/broken/ does not exist",
            ),
        ),
    )
    out = render_text(report)
    assert "PROJECT ERROR (broken)" in out
    assert "wiki/broken/ does not exist" in out


# ─── render_json ───────────────────────────────────────────────────────────


def test_render_json_shape(tmp_path: Path) -> None:
    out = render_json(
        _build_report(findings=[_err(), _warn(), _info()], wiki_dir=tmp_path)
    )
    payload = json.loads(out)
    assert payload["skill_version"] == "1.2.3"
    assert payload["wiki_dir"] == str(tmp_path)
    assert len(payload["projects"]) == 1
    assert payload["projects"][0]["name"] == "myproj"
    assert payload["projects"][0]["page_count"] == 10
    assert len(payload["projects"][0]["findings"]) == 3
    assert payload["summary"] == {"errors": 1, "warnings": 1, "info": 1}


def test_render_json_severity_strings_are_uppercase(tmp_path: Path) -> None:
    out = render_json(_build_report(findings=[_err()], wiki_dir=tmp_path))
    payload = json.loads(out)
    assert payload["projects"][0]["findings"][0]["severity"] == "ERROR"


def test_render_json_severity_filter(tmp_path: Path) -> None:
    out = render_json(
        _build_report(findings=[_err(), _warn(), _info()], wiki_dir=tmp_path),
        severity_filter=Severity.ERROR,
    )
    payload = json.loads(out)
    findings = payload["projects"][0]["findings"]
    assert all(f["severity"] == "ERROR" for f in findings)
    assert len(findings) == 1


def test_render_json_clean_report_empty_findings(tmp_path: Path) -> None:
    payload = json.loads(render_json(_build_report(findings=[], wiki_dir=tmp_path)))
    assert payload["projects"][0]["findings"] == []
    assert payload["summary"] == {"errors": 0, "warnings": 0, "info": 0}


# ─── compute_exit_code ─────────────────────────────────────────────────────


def test_exit_code_clean_returns_zero(tmp_path: Path) -> None:
    assert compute_exit_code(
        _build_report(findings=[], wiki_dir=tmp_path), Severity.INFO
    ) == 0


def test_exit_code_findings_present_returns_one(tmp_path: Path) -> None:
    assert compute_exit_code(
        _build_report(findings=[_err()], wiki_dir=tmp_path), Severity.INFO
    ) == 1


def test_exit_code_filtered_below_threshold_returns_zero(tmp_path: Path) -> None:
    """INFO-only findings + --severity warn → exit 0."""
    assert compute_exit_code(
        _build_report(findings=[_info()], wiki_dir=tmp_path), Severity.WARN
    ) == 0


def test_severity_from_string_aliases() -> None:
    assert Severity.from_string("ERROR") == Severity.ERROR
    assert Severity.from_string("warn") == Severity.WARN
    assert Severity.from_string("warning") == Severity.WARN
    assert Severity.from_string("info") == Severity.INFO
