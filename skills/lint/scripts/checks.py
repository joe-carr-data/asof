"""The 7 lint checks per PLAN.md §6.3.

Each check is a function `check_<id>(pages, project_ctx) -> list[Finding]`.
`pages` is the list of every parsed wiki page in the project; `project_ctx`
is the per-project context (paths, thresholds, raw_dir set, etc.).

Checks are pure: they read the parsed page tree + filesystem (for path-mismatch
and orphan-page) but never write. The `--fix` path is implemented separately
in fix.py.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
from pathlib import Path

from frontmatter import line_of_field
from model import Finding, ParsedPage, Severity

# Required frontmatter fields per SCHEMA.md §3.
REQUIRED_FIELDS: tuple[str, ...] = ("title", "type", "project", "last_updated")

# Source-summary pages have an extra requirement: at least one source entry
# with both `path` and `source_mtime`.
SOURCE_SUMMARY_TYPE = "source-summary"

# Removed-source marker per SCHEMA.md §6.4.
REMOVED_SOURCE_MARKER = "<!-- backing source removed -->"

# Supersession-note pattern. SCHEMA documents either an explicit "Self-supersession"
# header or "Previously X — superseded by Y" prose. We're forgiving: any of these
# strings (case-insensitive substring match) counts as a supersession note.
_SUPERSESSION_PATTERNS = (
    re.compile(r"superseded\s+by", re.IGNORECASE),
    re.compile(r"previously\s+\w+\s*[—-]", re.IGNORECASE),
    re.compile(r"##\s*self-supersession", re.IGNORECASE),
    re.compile(r"##\s*supersession", re.IGNORECASE),
)


@dataclasses.dataclass(frozen=True)
class ProjectContext:
    """Per-project context passed to every check.

    `wiki_dir`, `project_dir`, and `raw_dir` are absolute paths.
    `wiki_dir_for_relative` is what we relativize page paths against for
    Finding.page (typically `wiki_dir`, so paths look like
    `wiki/myproj/foo.md`).
    """

    project_name: str
    wiki_dir: Path  # absolute root of the wiki
    project_dir: Path  # absolute path to wiki/<project>/
    raw_project_dir: Path  # absolute path to raw/<project>/
    mtime_drift_days: int
    supersession_gap_days: int
    today: datetime.date


# ─── helpers ───────────────────────────────────────────────────────────────


def _parse_iso_date(value: object) -> datetime.date | None:
    """Parse an ISO-8601 date string. Returns None on any failure."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return None


def _is_removed_upstream(page: ParsedPage) -> bool:
    """True iff the page's frontmatter has `removed_upstream:` set —
    the SCHEMA-defined marker that the source no longer exists in raw/.
    Such pages are intentional historical record and exempt from
    path-mismatch and missing-mtime checks."""
    return "removed_upstream" in page.frontmatter


# ─── 1. frontmatter validity (ERROR) ───────────────────────────────────────


def check_frontmatter(
    pages: list[ParsedPage], ctx: ProjectContext
) -> list[Finding]:
    """Required fields per SCHEMA §3 + source-summary extras.

    Reports one finding per missing required field per page (so the user
    sees every problem, not just the first). Source-summary pages have
    extra requirements; non-source-summary pages can have empty sources.
    """
    findings: list[Finding] = []
    for page in pages:
        if not page.frontmatter:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    check="frontmatter",
                    page=page.relative_path,
                    line=1,
                    message="page has no frontmatter (expected `---` fence at line 1)",
                )
            )
            continue
        for field in REQUIRED_FIELDS:
            if field not in page.frontmatter or not page.frontmatter.get(field):
                line = line_of_field(page.raw_text, field) or 1
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        check="frontmatter",
                        page=page.relative_path,
                        line=line,
                        message=f"missing required field {field!r}",
                    )
                )
        # last_updated must be parseable as ISO date if present.
        last_updated = page.frontmatter.get("last_updated")
        if last_updated and _parse_iso_date(last_updated) is None:
            line = line_of_field(page.raw_text, "last_updated") or 1
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    check="frontmatter",
                    page=page.relative_path,
                    line=line,
                    message=(
                        f"last_updated {last_updated!r} is not a valid ISO date "
                        "(expected YYYY-MM-DD)"
                    ),
                )
            )
        # Source-summary pages must have non-empty sources.
        if page.frontmatter.get("type") == SOURCE_SUMMARY_TYPE:
            sources = page.frontmatter.get("sources") or []
            if not isinstance(sources, list) or not sources:
                line = line_of_field(page.raw_text, "sources") or 1
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        check="frontmatter",
                        page=page.relative_path,
                        line=line,
                        message=(
                            "source-summary page has no `sources` entries "
                            "(must cite at least one raw document)"
                        ),
                    )
                )
    return findings


# ─── 2. path mismatch (ERROR) ──────────────────────────────────────────────


def check_path_mismatch(
    pages: list[ParsedPage], ctx: ProjectContext
) -> list[Finding]:
    """Source-summary pages whose `sources[].path` doesn't exist under raw/.

    Skips pages with `removed_upstream:` set — those are SCHEMA-sanctioned
    historical record. Path is interpreted as relative to `wiki_dir`
    (matching the SCHEMA convention `path: raw/<project>/foo.md`).
    """
    findings: list[Finding] = []
    for page in pages:
        if _is_removed_upstream(page):
            continue
        sources = page.frontmatter.get("sources") or []
        if not isinstance(sources, list):
            continue
        line = line_of_field(page.raw_text, "sources") or 1
        for entry in sources:
            if not isinstance(entry, dict):
                continue
            raw_rel = entry.get("path")
            if not isinstance(raw_rel, str) or not raw_rel:
                continue
            # `path:` is wiki-dir-relative per SCHEMA (e.g. raw/myproj/foo.md).
            absolute = ctx.wiki_dir / raw_rel
            if not absolute.is_file():
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        check="path-mismatch",
                        page=page.relative_path,
                        line=line,
                        message=(
                            f"sources[].path {raw_rel!r} does not exist under "
                            f"{ctx.wiki_dir!s}"
                        ),
                    )
                )
    return findings


# ─── 3. missing mtime (ERROR) ──────────────────────────────────────────────


def check_missing_mtime(
    pages: list[ParsedPage], ctx: ProjectContext
) -> list[Finding]:
    """Source-summary pages with sources entries lacking source_mtime.

    Distinct from `frontmatter` check (which fires on missing `sources`
    entirely). This fires per-entry within a non-empty `sources` block.
    """
    findings: list[Finding] = []
    for page in pages:
        if _is_removed_upstream(page):
            continue
        if page.frontmatter.get("type") != SOURCE_SUMMARY_TYPE:
            continue
        sources = page.frontmatter.get("sources") or []
        if not isinstance(sources, list):
            continue
        line = line_of_field(page.raw_text, "sources") or 1
        for idx, entry in enumerate(sources):
            if not isinstance(entry, dict):
                continue
            mtime = entry.get("source_mtime")
            if not mtime:
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        check="missing-mtime",
                        page=page.relative_path,
                        line=line,
                        message=f"sources[{idx}] is missing source_mtime",
                    )
                )
            elif _parse_iso_date(mtime) is None:
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        check="missing-mtime",
                        page=page.relative_path,
                        line=line,
                        message=(
                            f"sources[{idx}].source_mtime {mtime!r} is not a "
                            "valid ISO date (expected YYYY-MM-DD)"
                        ),
                    )
                )
    return findings


# ─── 4. removed-source claims (WARN) ───────────────────────────────────────


def check_removed_source(
    pages: list[ParsedPage], ctx: ProjectContext
) -> list[Finding]:
    """Pages with the `<!-- backing source removed -->` marker.

    Per SCHEMA §6.5 these are intentional (the source disappeared upstream
    but the summary is preserved as historical record). Lint reports them
    as WARN so the agent can decide whether the page needs a successor or
    related-page update.
    """
    findings: list[Finding] = []
    for page in pages:
        if REMOVED_SOURCE_MARKER not in page.body:
            continue
        # Find the line within the body for the finding.
        body_offset_idx = page.body.find(REMOVED_SOURCE_MARKER)
        body_line_offset = page.body[:body_offset_idx].count("\n") + 1
        # Body starts after the closing `---` fence. Compute the absolute
        # line by counting frontmatter lines + 1 for the closing fence.
        fm_lines = page.raw_text.split("\n")
        # Find the second `---` to know where body starts.
        fence_count = 0
        body_start_line = 0
        for i, ln in enumerate(fm_lines):
            if ln.strip() == "---":
                fence_count += 1
                if fence_count == 2:
                    body_start_line = i + 2  # +1 for next line, +1 for 1-index
                    break
        line = body_start_line + body_line_offset - 1 if body_start_line else None
        findings.append(
            Finding(
                severity=Severity.WARN,
                check="removed-source",
                page=page.relative_path,
                line=line,
                message=(
                    "page contains `<!-- backing source removed -->` marker — "
                    "review whether dependent pages need updating"
                ),
            )
        )
    return findings


# ─── 5. mtime drift (WARN) ─────────────────────────────────────────────────


def check_mtime_drift(
    pages: list[ParsedPage], ctx: ProjectContext
) -> list[Finding]:
    """Pages where last_updated is older than newest cited source_mtime
    by more than ctx.mtime_drift_days.

    The intuition: the wiki page should have been re-read recently relative
    to the freshest source it cites. A long drift suggests the page hasn't
    been re-synthesized after the source moved.

    Skipped for: pages without parseable last_updated (frontmatter check
    fires); pages with no sources or unparseable source_mtimes; pages
    marked `removed_upstream:` (the source is gone, drift is meaningless).
    """
    findings: list[Finding] = []
    for page in pages:
        if _is_removed_upstream(page):
            continue
        last_updated = _parse_iso_date(page.frontmatter.get("last_updated"))
        if last_updated is None:
            continue
        sources = page.frontmatter.get("sources") or []
        if not isinstance(sources, list):
            continue
        mtimes: list[datetime.date] = []
        for entry in sources:
            if not isinstance(entry, dict):
                continue
            d = _parse_iso_date(entry.get("source_mtime"))
            if d is not None:
                mtimes.append(d)
        if not mtimes:
            continue
        newest = max(mtimes)
        drift = (newest - last_updated).days
        if drift > ctx.mtime_drift_days:
            line = line_of_field(page.raw_text, "last_updated") or 1
            findings.append(
                Finding(
                    severity=Severity.WARN,
                    check="mtime-drift",
                    page=page.relative_path,
                    line=line,
                    message=(
                        f"last_updated {last_updated.isoformat()} is "
                        f"{drift}d older than newest source_mtime "
                        f"{newest.isoformat()} (threshold: "
                        f"{ctx.mtime_drift_days}d)"
                    ),
                )
            )
    return findings


# ─── 6. supersession gap (WARN) ────────────────────────────────────────────


def check_supersession_gap(
    pages: list[ParsedPage], ctx: ProjectContext
) -> list[Finding]:
    """Pages citing two sources whose source_mtime differ by ≥
    ctx.supersession_gap_days, with no supersession note in the body.

    Logic: when a page mixes a fresh source with an old one, it should
    explicitly note which view is current. A missing note suggests stale
    claims may be passed off as authoritative.

    Skipped: pages with `removed_upstream:`, pages with <2 parseable mtimes.
    """
    findings: list[Finding] = []
    for page in pages:
        if _is_removed_upstream(page):
            continue
        sources = page.frontmatter.get("sources") or []
        if not isinstance(sources, list):
            continue
        mtimes: list[datetime.date] = []
        for entry in sources:
            if not isinstance(entry, dict):
                continue
            d = _parse_iso_date(entry.get("source_mtime"))
            if d is not None:
                mtimes.append(d)
        if len(mtimes) < 2:
            continue
        gap_days = (max(mtimes) - min(mtimes)).days
        if gap_days < ctx.supersession_gap_days:
            continue
        # Has a supersession note?
        if any(p.search(page.body) for p in _SUPERSESSION_PATTERNS):
            continue
        line = line_of_field(page.raw_text, "sources") or 1
        findings.append(
            Finding(
                severity=Severity.WARN,
                check="supersession-gap",
                page=page.relative_path,
                line=line,
                message=(
                    f"page cites sources spanning {gap_days}d (threshold: "
                    f"{ctx.supersession_gap_days}d) but body has no "
                    "supersession note (looked for 'superseded by' / "
                    "'Previously X —' / '## Self-supersession' / "
                    "'## Supersession')"
                ),
            )
        )
    return findings


# ─── 7. orphan pages (INFO) ────────────────────────────────────────────────

# Markdown link pattern: [Title](relative-path.md) or [Title](path/to/page.md)
# Tolerates anchors, query strings, and surrounding whitespace.
_LINK_RE = re.compile(r"\[(?P<title>[^\]]+)\]\((?P<href>[^)]+)\)")

# Bookkeeping pages exempt from "orphan" — they exist for the agent's bookkeeping
# even if not linked from index.md.
_BOOKKEEPING_PAGES: tuple[str, ...] = (
    "index.md",
    "log.md",
    "_candidates.md",
    "current_state.md",
)


def check_orphan_pages(
    pages: list[ParsedPage], ctx: ProjectContext
) -> list[Finding]:
    """Pages with no inbound link from any other wiki page in this project.

    Per PLAN.md §6.3: bookkeeping pages (index.md, log.md, _candidates.md,
    current_state.md) are exempt — they're rooted by convention.

    Implementation: build the set of inbound link targets across every page's
    body, then flag pages whose project-relative path isn't a link target.
    Resolves links relative to the linking page's directory, then matches
    against ctx.project_dir-relative paths.
    """
    if not pages:
        return []

    # Build inbound-link target set: every (project-relative path) that
    # any page in this project links to. We include both raw href and
    # the resolved path-relative-to-project for robust matching.
    inbound: set[str] = set()
    for page in pages:
        page_dir = page.absolute_path.parent
        for m in _LINK_RE.finditer(page.body):
            href = m.group("href").split("#", 1)[0].split("?", 1)[0].strip()
            if not href or href.startswith(("http://", "https://", "mailto:")):
                continue
            # Resolve href relative to the linking page's dir, then
            # express relative to project_dir for matching.
            try:
                target = (page_dir / href).resolve()
            except (OSError, ValueError):
                continue
            try:
                rel = target.relative_to(ctx.project_dir.resolve())
            except ValueError:
                # Link points outside project_dir; ignore for orphan detection.
                continue
            inbound.add(rel.as_posix())

    findings: list[Finding] = []
    for page in pages:
        if page.project_relative_path in _BOOKKEEPING_PAGES:
            continue
        if page.project_relative_path not in inbound:
            findings.append(
                Finding(
                    severity=Severity.INFO,
                    check="orphan-page",
                    page=page.relative_path,
                    line=None,
                    message=(
                        "no inbound links from index.md or other wiki pages "
                        "in this project"
                    ),
                )
            )
    return findings


# ─── orchestrator ──────────────────────────────────────────────────────────


def run_all_checks(
    pages: list[ParsedPage], ctx: ProjectContext
) -> list[Finding]:
    """Run every check and return aggregated findings.

    Order matches PLAN.md §6.3 numbered list. Findings are not deduplicated;
    a single page can produce multiple findings across checks (the renderer
    groups by severity, not by page).
    """
    findings: list[Finding] = []
    findings.extend(check_frontmatter(pages, ctx))
    findings.extend(check_path_mismatch(pages, ctx))
    findings.extend(check_missing_mtime(pages, ctx))
    findings.extend(check_removed_source(pages, ctx))
    findings.extend(check_mtime_drift(pages, ctx))
    findings.extend(check_supersession_gap(pages, ctx))
    findings.extend(check_orphan_pages(pages, ctx))
    return findings
