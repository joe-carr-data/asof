"""Narrow auto-fix path per PLAN.md §6.3.

Only 2 fixable cases:
  1. **Frontmatter validity** — when `last_updated` is missing ENTIRELY,
     insert today's date. Refused when the field exists with any value
     (overwriting a stale-but-present value would lie about edit recency).
  2. **Orphan pages** — append a one-line `- [Title](relative-path.md)`
     entry to `index.md` under the matching `## <type-section>` header.
     Refused when the page lacks a parseable title or type, or when the
     appropriate section doesn't exist in `index.md`.

All other findings are report-only. `--fix` does NOT edit page bodies,
rewrite stale `last_updated`, add supersession notes, delete pages, or
touch `current_state.md` / `log.md` / `_candidates.md`.

Read-only mode (compat-matrix cell b) is checked before any fix runs.
Lint exits 4 with an upgrade message rather than applying fixes.

All writes are atomic via `atomic_write_text` + same `<wiki_dir>/.asof.lock`
held by sync.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import datetime
import re
from pathlib import Path

from _lint_bridge import atomic_write_text
from model import Finding, ParsedPage


@dataclasses.dataclass(frozen=True)
class FixResult:
    """What apply_fixes() did — separates applied fixes from refusals so the
    caller can show the user exactly which findings still need attention.

    `applied` and `refused` partition the input findings on the auto-fixable
    subset. Findings that fall outside the 2 auto-fixable cases (e.g. a
    path-mismatch ERROR) are absent from both — they're never "fixable" by
    lint and are left to the user.
    """

    applied: tuple[Finding, ...]
    refused: tuple[tuple[Finding, str], ...]  # (finding, reason)


# ─── 1. missing last_updated → insert today ─────────────────────────────────


_LAST_UPDATED_LINE = re.compile(
    r"^last_updated:\s*(?P<value>.*)$", re.MULTILINE
)
# Matches a closing frontmatter fence at the start of a line (used as the
# insertion site when last_updated is entirely missing).
_CLOSING_FENCE = re.compile(r"^---\s*$", re.MULTILINE)


def _try_insert_last_updated(
    page: ParsedPage, today: datetime.date, *, dry_run: bool
) -> tuple[bool, str | None]:
    """Insert `last_updated: <today>` immediately before the closing fence.

    Returns (applied, refusal_reason). `applied=True` means the page was
    rewritten (or would be rewritten in dry-run); `applied=False` plus a
    refusal_reason means we deliberately declined.
    """
    if not page.frontmatter:
        return (False, "page has no frontmatter (cannot fix)")
    # Refuse if last_updated already exists with any value (even empty string).
    if "last_updated" in page.frontmatter:
        return (False, "last_updated already exists (refusing to overwrite)")
    # Find the closing fence in the raw text and inject before it.
    text = page.raw_text
    fences = list(_CLOSING_FENCE.finditer(text))
    if len(fences) < 2:
        return (False, "page has no closing frontmatter fence")
    closing = fences[1]  # second `---` is the closing fence
    insert_line = f"last_updated: {today.isoformat()}\n"
    new_text = text[: closing.start()] + insert_line + text[closing.start() :]
    if not dry_run:
        atomic_write_text(page.absolute_path, new_text)
    return (True, None)


# ─── 2. orphan-page → append entry to index.md ─────────────────────────────


# Page type → index.md section heading. Keys match SCHEMA's `type:` enum.
_TYPE_TO_SECTION: dict[str, str] = {
    "entity": "## Entities",
    "concept": "## Concepts",
    "source-summary": "## Source summaries",
    "comparison": "## Comparisons",
    "overview": "## Overviews",
    "synthesis": "## Syntheses",
}


def _try_append_orphan_to_index(
    page: ParsedPage,
    index_path: Path,
    *,
    dry_run: bool,
) -> tuple[bool, str | None]:
    """Append `- [Title](relative-path.md)` to the matching `## <Type>`
    section of index.md.

    Refuses when:
      - page lacks parseable `title` or `type` in frontmatter
      - title is not a non-empty string
      - the matching `## <Type>` section doesn't exist in index.md
      - index.md is missing entirely
    """
    title = page.frontmatter.get("title")
    page_type = page.frontmatter.get("type")
    if not isinstance(title, str) or not title.strip():
        return (False, "page has no parseable `title` field")
    if not isinstance(page_type, str) or not page_type:
        return (False, "page has no parseable `type` field")
    section_heading = _TYPE_TO_SECTION.get(page_type)
    if section_heading is None:
        return (False, f"unknown page type {page_type!r} (no matching index section)")
    if not index_path.is_file():
        return (False, f"index.md not found at {index_path!s}")
    index_text = index_path.read_text(encoding="utf-8")
    # Find the section heading and the next `## ` heading (or EOF) to know
    # where to append.
    heading_idx = index_text.find(section_heading)
    if heading_idx < 0:
        return (
            False,
            f"index.md has no {section_heading!r} section (cannot place entry)",
        )
    # Walk to end of the section.
    after_heading = index_text[heading_idx + len(section_heading) :]
    next_section_offset = re.search(r"\n##\s", after_heading)
    section_end = (
        heading_idx + len(section_heading) + next_section_offset.start()
        if next_section_offset
        else len(index_text)
    )
    section_body = index_text[heading_idx + len(section_heading) : section_end]
    # Compute the link href: project_relative_path is relative to project_dir,
    # which is also the dir containing index.md. So href = project_relative_path.
    href = page.project_relative_path
    new_entry = f"- [{title}]({href})\n"
    # Avoid duplicate entries (idempotent).
    if f"]({href})" in section_body:
        return (False, f"index.md already references {href!r} in {section_heading}")
    # Insert the entry at the end of the section body, ensuring exactly
    # one trailing newline before the next heading (or EOF).
    new_section_body = section_body.rstrip("\n") + "\n" + new_entry
    if next_section_offset:
        new_section_body = new_section_body.rstrip("\n") + "\n\n"
    new_text = (
        index_text[: heading_idx + len(section_heading)]
        + new_section_body
        + index_text[section_end:]
    )
    if not dry_run:
        atomic_write_text(index_path, new_text)
    return (True, None)


# ─── orchestrator ──────────────────────────────────────────────────────────


def apply_fixes(
    findings: list[Finding],
    pages_by_relpath: dict[str, ParsedPage],
    project_dirs: dict[str, Path],
    today: datetime.date,
    *,
    dry_run: bool = False,
) -> FixResult:
    """Apply the 2 narrow auto-fixes per PLAN.md §6.3.

    `pages_by_relpath` maps the wiki-relative page path (Finding.page) to
    the parsed page. `project_dirs` maps project name → wiki/<project>/ abs
    path (used to locate each project's index.md).

    Findings outside the 2 fixable cases are silently ignored — they're
    not auto-fixable by design. The renderer reports them separately so
    the user knows what's left for them.
    """
    applied: list[Finding] = []
    refused: list[tuple[Finding, str]] = []

    for f in findings:
        # Case 1: frontmatter ERROR with message indicating last_updated missing.
        # The check fires per-missing-field, so message includes "'last_updated'".
        if f.check == "frontmatter" and "'last_updated'" in f.message:
            page = pages_by_relpath.get(f.page)
            if page is None:
                refused.append((f, "page not found in parsed-pages map"))
                continue
            ok, reason = _try_insert_last_updated(page, today, dry_run=dry_run)
            if ok:
                applied.append(f)
            else:
                refused.append((f, reason or "unknown refusal"))
            continue

        # Case 2: orphan-page INFO.
        if f.check == "orphan-page":
            page = pages_by_relpath.get(f.page)
            if page is None:
                refused.append((f, "page not found in parsed-pages map"))
                continue
            project = page.frontmatter.get("project")
            if not isinstance(project, str) or project not in project_dirs:
                refused.append(
                    (f, "page has no resolvable project for index.md placement")
                )
                continue
            index_path = project_dirs[project] / "index.md"
            ok, reason = _try_append_orphan_to_index(
                page, index_path, dry_run=dry_run
            )
            if ok:
                applied.append(f)
            else:
                refused.append((f, reason or "unknown refusal"))
            continue

        # All other findings are NOT auto-fixable — silently skip; they're
        # absent from FixResult.applied AND .refused (the renderer treats
        # the remainder as "still reported").

    return FixResult(applied=tuple(applied), refused=tuple(refused))
