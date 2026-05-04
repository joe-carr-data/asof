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


def test_frontmatter_source_summary_must_cite_exactly_one_source(
    tmp_path: Path,
) -> None:
    """Codex round-1 phase-4 CRITICAL: source-summary pages must cite
    exactly one raw document (SCHEMA §3 line 113). Two sources → ERROR."""
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n"
        "  - path: raw/myproj/a.md\n    source_mtime: 2026-04-22\n    ingested: 2026-04-22\n"
        "  - path: raw/myproj/b.md\n    source_mtime: 2026-04-23\n    ingested: 2026-04-23\n"
        "---\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert any("cites 2 sources" in f.message for f in findings)


def test_frontmatter_source_summary_requires_path(tmp_path: Path) -> None:
    """Codex round-1 phase-4 CRITICAL: sources[0] must have `path`."""
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - source_mtime: 2026-04-22\n    ingested: 2026-04-22\n---\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert any("missing required field 'path'" in f.message for f in findings)


def test_frontmatter_source_summary_requires_ingested(tmp_path: Path) -> None:
    """Codex round-1 phase-4 CRITICAL: sources[0] must have `ingested`."""
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-04-22\n---\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert any("missing required field 'ingested'" in f.message for f in findings)


def test_frontmatter_source_summary_invalid_ingested_iso(tmp_path: Path) -> None:
    """sources[0].ingested must be parseable ISO date."""
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-04-22\n"
        "    ingested: not-a-date\n---\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert any("ingested" in f.message and "not a valid ISO date" in f.message
               for f in findings)


def test_frontmatter_removed_upstream_must_be_iso_date(tmp_path: Path) -> None:
    """Codex round-1 phase-4 HIGH: empty / unparseable removed_upstream
    is now an ERROR (previously silently bypassed downstream checks)."""
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "removed_upstream: not-a-date\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-04-22\n"
        "    ingested: 2026-04-22\n---\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert any("removed_upstream" in f.message and "not a valid ISO date" in f.message
               for f in findings)


def test_frontmatter_removed_upstream_only_on_source_summary(
    tmp_path: Path,
) -> None:
    """Codex round-1 phase-4 HIGH: removed_upstream is only valid on
    source-summary pages per SCHEMA §6.5. An entity page with
    `removed_upstream:` set produces a frontmatter ERROR."""
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n"
        "removed_upstream: 2026-04-29\n---\n",
    )
    findings = check_frontmatter([page], _make_ctx(tmp_path))
    assert any("only valid on source-summary" in f.message for f in findings)


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


def test_path_mismatch_rejects_absolute_path(tmp_path: Path) -> None:
    """Codex round-1 phase-4 HIGH: absolute paths must be rejected as a
    SCHEMA §3 contract violation, even if the absolute path exists."""
    # Create a file at an absolute location that exists.
    elsewhere = tmp_path / "elsewhere" / "foo.md"
    elsewhere.parent.mkdir()
    elsewhere.write_text("real file")
    page = _make_page(
        tmp_path,
        "sources/foo.md",
        f"---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        f"sources:\n  - path: {elsewhere!s}\n    source_mtime: 2026-04-22\n---\n",
    )
    findings = check_path_mismatch([page], _make_ctx(tmp_path))
    assert any("is absolute" in f.message for f in findings)


def test_path_mismatch_rejects_path_traversal(tmp_path: Path) -> None:
    """Codex round-1 phase-4 HIGH: `../` paths that escape raw_project_dir
    are rejected even when the resolved target exists."""
    # File one level above raw_project_dir that the traversal could "find".
    above = tmp_path / "above.md"
    above.write_text("attacker-controlled")
    page = _make_page(
        tmp_path,
        "sources/foo.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n"
        "sources:\n  - path: raw/myproj/../../above.md\n    source_mtime: 2026-04-22\n---\n",
    )
    findings = check_path_mismatch([page], _make_ctx(tmp_path))
    assert any("escapes" in f.message and "path-traversal" in f.message
               for f in findings)


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


def test_removed_source_dated_marker_fires(tmp_path: Path) -> None:
    """Codex round-2 phase-4 HIGH: SCHEMA's canonical marker is dated
    (`<!-- backing source removed: 2026-04-26 -->`). Round-1 code only
    matched the undated form, so schema-conformant pages slipped past."""
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n---\n"
        "Body line.\n<!-- backing source removed: 2026-04-26 -->\nMore.\n",
    )
    findings = check_removed_source([page], _make_ctx(tmp_path))
    assert len(findings) == 1
    assert findings[0].check == "removed-source"
    assert findings[0].severity == Severity.WARN


def test_removed_source_marker_whitespace_tolerant(tmp_path: Path) -> None:
    """Marker regex is whitespace- and case-tolerant so minor formatting
    quirks don't silently bypass the WARN."""
    page = _make_page(
        tmp_path,
        "sources/x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-04-26\n---\n"
        "<!--   Backing Source Removed  :  2026-04-26   -->\n",
    )
    findings = check_removed_source([page], _make_ctx(tmp_path))
    assert len(findings) == 1


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
    """removed_upstream skip requires type=source-summary AND parseable
    ISO date (Codex round-1 phase-4 HIGH). With both → skipped."""
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-01-01\n"
        "removed_upstream: 2026-04-29\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-05-04\n---\n",
    )
    findings = check_mtime_drift([page], _make_ctx(tmp_path))
    assert findings == []


def test_mtime_drift_does_not_skip_when_removed_upstream_invalid(
    tmp_path: Path,
) -> None:
    """An entity page with removed_upstream is NOT exempt — that key is
    only valid on source-summary pages per SCHEMA §6.5. Codex round-1
    phase-4 HIGH: previous code accepted mere key presence."""
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-01-01\n"
        "removed_upstream: 2026-04-29\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-05-04\n---\n",
    )
    findings = check_mtime_drift([page], _make_ctx(tmp_path))
    # Entity page with removed_upstream is not skipped; mtime-drift fires.
    assert any(f.check == "mtime-drift" for f in findings)


def test_mtime_drift_does_not_skip_when_removed_upstream_malformed(
    tmp_path: Path,
) -> None:
    """source-summary with malformed `removed_upstream:` (no value or
    unparseable) is NOT exempt — _is_removed_upstream now requires a
    parseable ISO date."""
    page = _make_page(
        tmp_path,
        "x.md",
        "---\ntitle: T\ntype: source-summary\nproject: p\nlast_updated: 2026-01-01\n"
        "removed_upstream: not-a-date\n"
        "sources:\n  - path: raw/myproj/foo.md\n    source_mtime: 2026-05-04\n"
        "    ingested: 2026-05-04\n---\n",
    )
    findings = check_mtime_drift([page], _make_ctx(tmp_path))
    assert any(f.check == "mtime-drift" for f in findings)


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
