"""Lint data model — Finding, Severity, ParsedPage, and report containers.

Every check in `checks.py` returns a list of `Finding`. The orchestrator
aggregates them into `ProjectReport` (per-project) and `LintReport`
(top-level), which the renderer (`render.py`) turns into text or JSON.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import enum
from pathlib import Path


class Severity(enum.Enum):
    """Lint finding severity. Maps to PLAN.md §6.3 table.

    Ordering matters: ERROR > WARN > INFO. The `--severity` flag filters
    to a threshold and above; `value` is used as the comparable rank.
    """

    ERROR = 3
    WARN = 2
    INFO = 1

    @classmethod
    def from_string(cls, raw: str) -> Severity:
        """Parse case-insensitive 'error' / 'warn' / 'info' (alias 'warning')."""
        normalized = raw.strip().lower()
        if normalized == "warning":
            normalized = "warn"
        try:
            return cls[normalized.upper()]
        except KeyError as exc:
            valid = ", ".join(s.name.lower() for s in cls)
            raise ValueError(
                f"unknown severity {raw!r} (valid: {valid})"
            ) from exc


# All check IDs (machine-readable identifiers for JSON output and tests).
# Per PLAN.md §6.3.
CHECK_IDS: tuple[str, ...] = (
    "frontmatter",        # 1: Frontmatter validity (ERROR)
    "path-mismatch",      # 2: Path mismatch (ERROR)
    "missing-mtime",      # 3: Missing mtime (ERROR)
    "removed-source",     # 4: Removed-source claims (WARN)
    "mtime-drift",        # 5: Mtime drift (WARN)
    "supersession-gap",   # 6: Supersession gap (WARN)
    "orphan-page",        # 7: Orphan pages (INFO)
)


@dataclasses.dataclass(frozen=True)
class Finding:
    """A single lint finding pinned to a page.

    `page` is the path relative to the wiki_dir (e.g. "wiki/myproj/x.md")
    so output is portable across different wiki_dir absolute paths.
    `line` is 1-indexed; None when the finding isn't tied to a specific line
    (e.g. orphan-page is a whole-page property).
    """

    severity: Severity
    check: str  # one of CHECK_IDS
    page: str  # relative to wiki_dir
    message: str
    line: int | None = None

    def __post_init__(self) -> None:
        if self.check not in CHECK_IDS:
            raise ValueError(
                f"unknown check id {self.check!r}; expected one of {CHECK_IDS}"
            )


@dataclasses.dataclass(frozen=True)
class ParsedPage:
    """Result of parsing a wiki page's frontmatter + body.

    `frontmatter` is the parsed YAML-ish dict (see frontmatter.py). Empty
    dict if the page has no frontmatter at all (a frontmatter-validity
    finding will fire elsewhere). `body` is everything after the closing
    `---` fence.

    `relative_path` is the page path relative to wiki_dir for portable
    output. `wiki_dir_relative_to_project` is the page path relative to
    the project's wiki_subdir (e.g. "concepts/x.md"), used for orphan-link
    detection and path-mismatch reporting.
    """

    absolute_path: Path
    relative_path: str  # e.g. "wiki/myproj/concepts/x.md"
    project_relative_path: str  # e.g. "concepts/x.md"
    frontmatter: dict
    body: str
    raw_text: str  # full file contents, for line-number lookups


@dataclasses.dataclass(frozen=True)
class ProjectReport:
    """Lint findings for a single project, plus context."""

    project_name: str
    page_count: int
    findings: tuple[Finding, ...]
    project_level_error: str | None = None  # e.g. wiki_subdir missing entirely


@dataclasses.dataclass(frozen=True)
class LintReport:
    """Top-level lint report — config + per-project findings."""

    wiki_dir: Path
    skill_version: str
    projects: tuple[ProjectReport, ...]

    @property
    def all_findings(self) -> tuple[Finding, ...]:
        out: list[Finding] = []
        for p in self.projects:
            out.extend(p.findings)
        return tuple(out)

    def summary_counts(self) -> dict[str, int]:
        """Aggregate counts by severity name for the summary footer / JSON."""
        counts = {"errors": 0, "warnings": 0, "info": 0}
        for f in self.all_findings:
            if f.severity == Severity.ERROR:
                counts["errors"] += 1
            elif f.severity == Severity.WARN:
                counts["warnings"] += 1
            elif f.severity == Severity.INFO:
                counts["info"] += 1
        return counts
