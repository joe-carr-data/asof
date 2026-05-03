"""Tests for skills/lint/scripts/fix.py — narrow --fix path.

The 2 fixable cases:
  1. frontmatter ERROR for missing `last_updated` → insert today.
  2. orphan-page INFO → append `- [Title](path)` to index.md.

Plus refusals (every other check, plus refused variants of the 2 cases).
"""

from __future__ import annotations

import datetime
from pathlib import Path

from fix import FixResult, apply_fixes
from frontmatter import parse_page
from model import Finding, ParsedPage, Severity

TODAY = datetime.date(2026, 5, 4)


def _make_page(
    project_dir: Path,
    relative: str,
    text: str,
    *,
    wiki_dir: Path,
) -> ParsedPage:
    abs_path = project_dir / relative
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(text, encoding="utf-8")
    fm, body, _ = parse_page(text)
    return ParsedPage(
        absolute_path=abs_path,
        relative_path=str(abs_path.relative_to(wiki_dir).as_posix()),
        project_relative_path=relative,
        frontmatter=fm,
        body=body,
        raw_text=text,
    )


# ─── case 1: insert missing last_updated ──────────────────────────────────


def test_fix_inserts_missing_last_updated(tmp_path: Path) -> None:
    project_dir = tmp_path / "wiki" / "myproj"
    page = _make_page(
        project_dir,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: myproj\n---\nBody.\n",
        wiki_dir=tmp_path,
    )
    finding = Finding(
        severity=Severity.ERROR,
        check="frontmatter",
        page=page.relative_path,
        line=1,
        message="missing required field 'last_updated'",
    )
    result = apply_fixes(
        [finding],
        {page.relative_path: page},
        {"myproj": project_dir},
        TODAY,
        dry_run=False,
    )
    assert len(result.applied) == 1
    assert result.refused == ()
    assert "last_updated: 2026-05-04" in page.absolute_path.read_text()


def test_fix_refuses_when_last_updated_already_present(tmp_path: Path) -> None:
    """Refuse to overwrite a stale-but-present last_updated."""
    project_dir = tmp_path / "wiki" / "myproj"
    page = _make_page(
        project_dir,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: myproj\nlast_updated: 2025-01-01\n---\nBody.\n",
        wiki_dir=tmp_path,
    )
    # Construct a finding for a different missing field.
    finding = Finding(
        severity=Severity.ERROR,
        check="frontmatter",
        page=page.relative_path,
        line=1,
        message="missing required field 'last_updated'",
    )
    result = apply_fixes(
        [finding],
        {page.relative_path: page},
        {"myproj": project_dir},
        TODAY,
        dry_run=False,
    )
    assert result.applied == ()
    assert len(result.refused) == 1
    assert "already exists" in result.refused[0][1]
    # File unchanged.
    assert page.absolute_path.read_text().count("last_updated") == 1


def test_fix_dry_run_does_not_write(tmp_path: Path) -> None:
    project_dir = tmp_path / "wiki" / "myproj"
    page = _make_page(
        project_dir,
        "x.md",
        "---\ntitle: T\ntype: entity\nproject: myproj\n---\nBody.\n",
        wiki_dir=tmp_path,
    )
    original = page.absolute_path.read_text()
    finding = Finding(
        severity=Severity.ERROR,
        check="frontmatter",
        page=page.relative_path,
        line=1,
        message="missing required field 'last_updated'",
    )
    apply_fixes(
        [finding],
        {page.relative_path: page},
        {"myproj": project_dir},
        TODAY,
        dry_run=True,
    )
    # File unchanged.
    assert page.absolute_path.read_text() == original


def test_fix_skips_unrelated_frontmatter_messages(tmp_path: Path) -> None:
    """A frontmatter finding for missing 'title' is NOT treated as a
    last_updated fix — the message includes 'title', not 'last_updated'."""
    project_dir = tmp_path / "wiki" / "myproj"
    page = _make_page(
        project_dir,
        "x.md",
        "---\ntype: entity\nproject: myproj\n---\nBody.\n",
        wiki_dir=tmp_path,
    )
    finding = Finding(
        severity=Severity.ERROR,
        check="frontmatter",
        page=page.relative_path,
        line=1,
        message="missing required field 'title'",
    )
    result = apply_fixes(
        [finding],
        {page.relative_path: page},
        {"myproj": project_dir},
        TODAY,
        dry_run=False,
    )
    # Not auto-fixable → absent from BOTH applied AND refused.
    assert result.applied == ()
    assert result.refused == ()


# ─── case 2: append orphan to index.md ────────────────────────────────────


def test_fix_appends_orphan_to_index_entities(tmp_path: Path) -> None:
    project_dir = tmp_path / "wiki" / "myproj"
    project_dir.mkdir(parents=True)
    (project_dir / "index.md").write_text(
        "---\ntitle: idx\ntype: overview\nproject: myproj\nlast_updated: 2026-04-26\n---\n"
        "## Entities\n\n## Concepts\n",
        encoding="utf-8",
    )
    page = _make_page(
        project_dir,
        "entities/lone.md",
        "---\ntitle: Lone\ntype: entity\nproject: myproj\nlast_updated: 2026-04-26\n---\nBody.\n",
        wiki_dir=tmp_path,
    )
    finding = Finding(
        severity=Severity.INFO,
        check="orphan-page",
        page=page.relative_path,
        line=None,
        message="no inbound links",
    )
    result = apply_fixes(
        [finding],
        {page.relative_path: page},
        {"myproj": project_dir},
        TODAY,
        dry_run=False,
    )
    assert len(result.applied) == 1
    index_text = (project_dir / "index.md").read_text()
    assert "[Lone](entities/lone.md)" in index_text
    # Entry placed under ## Entities, not ## Concepts.
    entities_section = index_text.split("## Entities")[1].split("## Concepts")[0]
    assert "[Lone]" in entities_section


def test_fix_orphan_idempotent_on_existing_entry(tmp_path: Path) -> None:
    """Already-linked page → refuse with 'already references'."""
    project_dir = tmp_path / "wiki" / "myproj"
    project_dir.mkdir(parents=True)
    (project_dir / "index.md").write_text(
        "---\ntitle: idx\ntype: overview\nproject: myproj\nlast_updated: 2026-04-26\n---\n"
        "## Entities\n- [Lone](entities/lone.md)\n",
        encoding="utf-8",
    )
    page = _make_page(
        project_dir,
        "entities/lone.md",
        "---\ntitle: Lone\ntype: entity\nproject: myproj\nlast_updated: 2026-04-26\n---\n",
        wiki_dir=tmp_path,
    )
    finding = Finding(
        severity=Severity.INFO, check="orphan-page", page=page.relative_path,
        line=None, message="...",
    )
    result = apply_fixes(
        [finding], {page.relative_path: page}, {"myproj": project_dir},
        TODAY, dry_run=False,
    )
    assert result.applied == ()
    assert any("already references" in reason for _, reason in result.refused)


def test_fix_orphan_refuses_missing_section(tmp_path: Path) -> None:
    """index.md has no ## Concepts heading → refuse for concept page."""
    project_dir = tmp_path / "wiki" / "myproj"
    project_dir.mkdir(parents=True)
    (project_dir / "index.md").write_text(
        "---\ntitle: idx\ntype: overview\nproject: myproj\nlast_updated: 2026-04-26\n---\n"
        "## Entities\n",  # ← no Concepts section
        encoding="utf-8",
    )
    page = _make_page(
        project_dir,
        "concepts/c.md",
        "---\ntitle: C\ntype: concept\nproject: myproj\nlast_updated: 2026-04-26\n---\n",
        wiki_dir=tmp_path,
    )
    finding = Finding(
        severity=Severity.INFO, check="orphan-page", page=page.relative_path,
        line=None, message="...",
    )
    result = apply_fixes(
        [finding], {page.relative_path: page}, {"myproj": project_dir},
        TODAY, dry_run=False,
    )
    assert result.applied == ()
    assert any("no '## Concepts'" in reason for _, reason in result.refused)


def test_fix_orphan_refuses_unparseable_title(tmp_path: Path) -> None:
    project_dir = tmp_path / "wiki" / "myproj"
    project_dir.mkdir(parents=True)
    (project_dir / "index.md").write_text(
        "---\ntitle: idx\n---\n## Entities\n", encoding="utf-8"
    )
    # Page with no title field.
    page = _make_page(
        project_dir,
        "entities/notitle.md",
        "---\ntype: entity\nproject: myproj\nlast_updated: 2026-04-26\n---\n",
        wiki_dir=tmp_path,
    )
    finding = Finding(
        severity=Severity.INFO, check="orphan-page", page=page.relative_path,
        line=None, message="...",
    )
    result = apply_fixes(
        [finding], {page.relative_path: page}, {"myproj": project_dir},
        TODAY, dry_run=False,
    )
    assert any("no parseable `title`" in reason for _, reason in result.refused)


# ─── non-fixable findings are silently skipped ────────────────────────────


def test_fix_silently_skips_non_fixable_checks(tmp_path: Path) -> None:
    """path-mismatch / mtime-drift / supersession-gap / etc. are NOT
    auto-fixable. Findings for those checks must be absent from both
    applied AND refused."""
    project_dir = tmp_path / "wiki" / "myproj"
    project_dir.mkdir(parents=True)
    findings = [
        Finding(severity=Severity.ERROR, check="path-mismatch",
                page="wiki/myproj/x.md", line=1, message="..."),
        Finding(severity=Severity.WARN, check="mtime-drift",
                page="wiki/myproj/y.md", line=1, message="..."),
        Finding(severity=Severity.WARN, check="supersession-gap",
                page="wiki/myproj/z.md", line=1, message="..."),
    ]
    result = apply_fixes(
        findings, {}, {"myproj": project_dir}, TODAY, dry_run=False
    )
    assert result.applied == ()
    assert result.refused == ()


def test_fix_returns_fix_result_dataclass(tmp_path: Path) -> None:
    """Type/shape sanity check."""
    result = apply_fixes([], {}, {}, TODAY, dry_run=False)
    assert isinstance(result, FixResult)
    assert result.applied == ()
    assert result.refused == ()
