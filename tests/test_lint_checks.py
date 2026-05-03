"""Tests for the 7 lint checks in skills/lint/scripts/checks.py.

Each check has its own section. Helpers at the top build ParsedPage
instances inline so individual tests stay readable.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from checks import (
    ProjectContext,
    check_frontmatter,
    check_missing_mtime,
    check_mtime_drift,
    check_orphan_pages,
    check_path_mismatch,
    check_removed_source,
    check_supersession_gap,
    run_all_checks,
)
from frontmatter import parse_page
from model import ParsedPage, Severity

TODAY = datetime.date(2026, 5, 4)

# ─── helpers ───────────────────────────────────────────────────────────────


def _make_page(
    tmp_path: Path,
    project_relative: str,
    text: str,
    *,
    project_subdir: str = "wiki/myproj",
) -> ParsedPage:
    """Build a ParsedPage rooted at <tmp_path>/<project_subdir>/<project_relative>."""
    project_dir = tmp_path / project_subdir
    project_dir.mkdir(parents=True, exist_ok=True)
    abs_path = project_dir / project_relative
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(text, encoding="utf-8")
    fm, body, _ = parse_page(text)
    return ParsedPage(
        absolute_path=abs_path,
        relative_path=f"{project_subdir}/{project_relative}",
        project_relative_path=project_relative,
        frontmatter=fm,
        body=body,
        raw_text=text,
    )


def _make_ctx(
    tmp_path: Path,
    *,
    project_subdir: str = "wiki/myproj",
    raw_subdir: str = "raw/myproj",
    mtime_drift_days: int = 30,
    supersession_gap_days: int = 60,
) -> ProjectContext:
    project_dir = tmp_path / project_subdir
    project_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = tmp_path / raw_subdir
    raw_dir.mkdir(parents=True, exist_ok=True)
    return ProjectContext(
        project_name="myproj",
        wiki_dir=tmp_path,
        project_dir=project_dir,
        raw_project_dir=raw_dir,
        mtime_drift_days=mtime_drift_days,
        supersession_gap_days=supersession_gap_days,
        today=TODAY,
    )


# ─── 1. frontmatter validity ───────────────────────────────────────────────


def test_frontmatter_clean_page_no_findings(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: X\ntype: entity\nproject: myproj\nlast_updated: 2026-04-26\n---\nbody\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert findings == []


def test_frontmatter_no_fence_fires(tmp_path: Path) -> None:
    page = _make_page(tmp_path, "x.md", "no fence at all\n")
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert len(findings) == 1
    assert findings[0].check == "frontmatter"
    assert findings[0].severity == Severity.ERROR
    assert "no frontmatter" in findings[0].message


def test_frontmatter_missing_title_and_project(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntype: entity\nlast_updated: 2026-04-26\n---\nbody\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    msgs = sorted(f.message for f in findings)
    assert any("'title'" in m for m in msgs)
    assert any("'project'" in m for m in msgs)


def test_frontmatter_unparseable_last_updated(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: not-a-date\n---\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert any("not a valid ISO date" in f.message for f in findings)


def test_frontmatter_source_summary_empty_sources(tmp_path: Path) -> None:
    """Source-summary pages must cite at least one source."""
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n---\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert any("no `sources` entries" in f.message for f in findings)


# ─── 2. path mismatch ──────────────────────────────────────────────────────


def test_path_mismatch_existing_source_no_finding(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "myproj" / "foo.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("source content")
    page = _make_page(
        tmp_path,
        "sources/foo.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-04-22\n---\n",
    )
    findings = check_path_mismatch([page], _make_ctx(tmp_path))
    assert findings == []


def test_path_mismatch_missing_source_fires(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "sources/foo.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/missing.md\n    source_mtime: 2026-04-22\n---\n",
    )
    findings = check_path_mismatch([page], _make_ctx(tmp_path))
    assert len(findings) == 1
    assert findings[0].check == "path-mismatch"
    assert findings[0].severity == Severity.ERROR


def test_path_mismatch_skips_removed_upstream(tmp_path: Path) -> None:
    """Pages with `removed_upstream:` are intentional historical record."""
    page = _make_page(
        tmp_path,
        "sources/foo.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "removed_upstream: 2026-04-29\n"
        "sources:\n  - path: raw/myproj/gone.md\n    source_mtime: 2026-04-22\n---\n",
    )
    findings = check_path_mismatch([page], _make_ctx(tmp_path))
    assert findings == []


# ─── 3. missing mtime ──────────────────────────────────────────────────────


def test_missing_mtime_present_no_finding(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-04-22\n---\n",
    )
    findings = check_missing_mtime([page], _make_ctx(tmp_path))
    assert findings == []


def test_missing_mtime_absent_fires(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/foo.md\n---\n",
    )
    findings = check_missing_mtime([page], _make_ctx(tmp_path))
    assert len(findings) == 1
    assert findings[0].check == "missing-mtime"


def test_missing_mtime_invalid_date_fires(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: not-a-date\n---\n",
    )
    findings = check_missing_mtime([page], _make_ctx(tmp_path))
    assert len(findings) == 1
    assert "not a valid ISO date" in findings[0].message


def test_missing_mtime_skips_non_source_summary(tmp_path: Path) -> None:
    """Non-source-summary pages don't get this check (they may cite many
    sources; the check fires only for source-summary type)."""
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/foo.md\n---\n",
    )
    findings = check_missing_mtime([page], _make_ctx(tmp_path))
    assert findings == []


# ─── 4. removed-source ─────────────────────────────────────────────────────


def test_removed_source_marker_fires(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n---\n"
        "Body line.\n<!-- backing source removed -->\nMore.\n",
    )
    findings = check_removed_source([page], _make_ctx(tmp_path))
    assert len(findings) == 1
    assert findings[0].check == "removed-source"
    assert findings[0].severity == Severity.WARN


def test_removed_source_no_marker_no_finding(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n---\nBody.\n",
    )
    findings = check_removed_source([page], _make_ctx(tmp_path))
    assert findings == []


# ─── 5. mtime drift ────────────────────────────────────────────────────────


def test_mtime_drift_under_threshold_no_finding(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-05-04\n---\n",
    )
    findings = check_mtime_drift([page], _make_ctx(tmp_path, mtime_drift_days=30))
    assert findings == []


def test_mtime_drift_over_threshold_fires(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-01-01\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-05-04\n---\n",
    )
    findings = check_mtime_drift([page], _make_ctx(tmp_path, mtime_drift_days=30))
    assert len(findings) == 1
    assert findings[0].check == "mtime-drift"
    assert "older" in findings[0].message


def test_mtime_drift_skips_removed_upstream(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-01-01\n"
        "removed_upstream: 2026-04-29\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-05-04\n---\n",
    )
    findings = check_mtime_drift([page], _make_ctx(tmp_path))
    assert findings == []


# ─── 6. supersession gap ───────────────────────────────────────────────────


def test_supersession_gap_with_note_no_finding(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/old.md\n    source_mtime: 2026-01-01\n"
        "  - path: raw/myproj/new.md\n    source_mtime: 2026-05-01\n---\n"
        "Old claim — superseded by new claim per latest source.\n",
    )
    findings = check_supersession_gap(
        [page], _make_ctx(tmp_path, supersession_gap_days=60)
    )
    assert findings == []


def test_supersession_gap_no_note_fires(tmp_path: Path) -> None:
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/old.md\n    source_mtime: 2026-01-01\n"
        "  - path: raw/myproj/new.md\n    source_mtime: 2026-05-01\n---\n"
        "Body without supersession note.\n",
    )
    findings = check_supersession_gap(
        [page], _make_ctx(tmp_path, supersession_gap_days=60)
    )
    assert len(findings) == 1
    assert findings[0].check == "supersession-gap"


def test_supersession_gap_under_threshold_no_finding(tmp_path: Path) -> None:
    """Two sources within threshold → no need for supersession note."""
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/a.md\n    source_mtime: 2026-04-20\n"
        "  - path: raw/myproj/b.md\n    source_mtime: 2026-04-25\n---\nBody.\n",
    )
    findings = check_supersession_gap(
        [page], _make_ctx(tmp_path, supersession_gap_days=60)
    )
    assert findings == []


# ─── 7. orphan pages ───────────────────────────────────────────────────────


def test_orphan_page_with_inbound_link_no_finding(tmp_path: Path) -> None:
    """Page linked from index.md is not orphan."""
    index = _make_page(
        tmp_path,
        "index.md",
        "---\ntitle: idx\ntype: overview\nproject: p\nlast_updated: 2026-04-26\n---\n"
        "## Entities\n- [X](entities/x.md)\n",
    )
    target = _make_page(
        tmp_path,
        "entities/x.md",
        "---\ntitle: X\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n---\nBody.\n",
    )
    findings = check_orphan_pages([index, target], _make_ctx(tmp_path))
    assert findings == []


def test_orphan_page_no_inbound_fires(tmp_path: Path) -> None:
    index = _make_page(
        tmp_path,
        "index.md",
        "---\ntitle: idx\ntype: overview\nproject: p\nlast_updated: 2026-04-26\n---\n## Entities\n",
    )
    orphan = _make_page(
        tmp_path,
        "entities/lone.md",
        "---\ntitle: Lone\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n---\nBody.\n",
    )
    findings = check_orphan_pages([index, orphan], _make_ctx(tmp_path))
    assert len(findings) == 1
    assert findings[0].check == "orphan-page"
    assert findings[0].severity == Severity.INFO


def test_orphan_page_bookkeeping_exempt(tmp_path: Path) -> None:
    """current_state.md / log.md / _candidates.md never reported as orphan."""
    index = _make_page(
        tmp_path,
        "index.md",
        "---\ntitle: idx\ntype: overview\nproject: p\nlast_updated: 2026-04-26\n---\n",
    )
    cs = _make_page(
        tmp_path,
        "current_state.md",
        "---\ntitle: cs\ntype: overview\nproject: p\nlast_updated: 2026-04-26\n---\n",
    )
    log = _make_page(
        tmp_path,
        "log.md",
        "---\ntitle: log\ntype: overview\nproject: p\nlast_updated: 2026-04-26\n---\n",
    )
    cands = _make_page(
        tmp_path,
        "_candidates.md",
        "---\ntitle: c\ntype: overview\nproject: p\nlast_updated: 2026-04-26\n---\n",
    )
    findings = check_orphan_pages([index, cs, log, cands], _make_ctx(tmp_path))
    assert findings == []


# ─── orchestrator ──────────────────────────────────────────────────────────


def test_run_all_checks_aggregates(tmp_path: Path) -> None:
    """Single page that triggers multiple checks → multiple findings aggregated."""
    page = _make_page(
        tmp_path,
        "x.md",
        # Missing project → frontmatter ERROR
        # Body has removed-source marker → removed-source WARN
        "---\ntitle: T\ntype: entity\nlast_updated: 2026-04-26\n---\n"
        "<!-- backing source removed -->\n",
    )
    findings = run_all_checks([page], _make_ctx(tmp_path))
    checks = {f.check for f in findings}
    assert "frontmatter" in checks
    assert "removed-source" in checks


@pytest.mark.parametrize(
    "severity",
    [Severity.ERROR, Severity.WARN, Severity.INFO],
)
def test_severity_rank_ordering(severity: Severity) -> None:
    """Severity .value matters for filter logic — confirm ordering."""
    assert Severity.ERROR.value > Severity.WARN.value > Severity.INFO.value
