"""Schema-vs-parser drift detection.

Codex phase-1 advice: ship a tiny schema fixture set and verify
delta.parse_sources / extract_frontmatter handle every shape SCHEMA.md
documents. Failure = the doc and the parser have drifted; either fix
the parser or update the doc, but don't ship them disagreeing.

These are NOT tests of parser behavior in isolation (that's
tests/test_delta.py). They are tests of the documented schema's
parseability. New schema features must add a fixture here so future
parser changes don't silently regress them.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from delta import build_source_index, extract_frontmatter, parse_sources

# ─── canonical fixtures (mirror SCHEMA.md §3 + §5) ─────────────────────────

#: Minimal source-summary frontmatter, exactly as SCHEMA.md §3 documents
#: for source-summary pages. Every field that's required + commonly used.
MINIMAL_SOURCE_SUMMARY = textwrap.dedent(
    """\
    ---
    title: Source — foo.md
    type: source-summary
    project: demo
    sources:
      - path: raw/demo/foo.md
        source_mtime: 2026-04-26
        ingested: 2026-04-26
    tags: [example]
    last_updated: 2026-04-26
    ---

    # foo.md (raw/demo/foo.md)

    Body.
    """
)

#: Source-summary with self-supersession (SCHEMA.md §5).
SELF_SUPERSEDED_SUMMARY = textwrap.dedent(
    """\
    ---
    title: Source — foo.md
    type: source-summary
    project: demo
    sources:
      - path: raw/demo/foo.md
        source_mtime: 2026-04-26
        ingested: 2026-04-26
    previous_mtimes:
      - 2026-02-05
    tags: [example, self-supersession]
    last_updated: 2026-04-26
    ---

    # foo.md

    ## Self-supersession (2026-04-26)

    Earlier version (mtime 2026-02-05) said X. Current version says Y.
    """
)

#: Source-summary with removed_upstream marker (SCHEMA.md §5).
REMOVED_UPSTREAM_SUMMARY = textwrap.dedent(
    """\
    ---
    title: Source — foo.md
    type: source-summary
    project: demo
    sources:
      - path: raw/demo/foo.md
        source_mtime: 2026-04-26
        ingested: 2026-04-26
    removed_upstream: 2026-04-30
    tags: [example, removed]
    last_updated: 2026-04-30
    ---

    ## Removed upstream (2026-04-30)

    The source was deleted upstream on this date.
    """
)

#: Source-summary citing a path with spaces (must be quoted per SCHEMA.md §3).
QUOTED_PATH_SUMMARY = textwrap.dedent(
    """\
    ---
    title: Source — foo bar.md
    type: source-summary
    project: demo
    sources:
      - path: "raw/demo/foo bar.md"
        source_mtime: 2026-04-26
        ingested: 2026-04-26
    last_updated: 2026-04-26
    ---

    Body.
    """
)

#: Entity page citing multiple sources (SCHEMA.md §3 — entity pages may
#: cite many sources).
ENTITY_MULTI_SOURCE = textwrap.dedent(
    """\
    ---
    title: foo (the system)
    type: entity
    project: demo
    sources:
      - path: raw/demo/docs/architecture.md
        source_mtime: 2026-03-15
        ingested: 2026-03-15
      - path: raw/demo/docs/runbook.md
        source_mtime: 2026-04-20
        ingested: 2026-04-20
      - path: raw/demo/CLAUDE.md
        source_mtime: 2026-04-26
        ingested: 2026-04-26
    tags: [system, infrastructure]
    last_updated: 2026-04-26
    ---

    Body.
    """
)

#: Bookkeeping page (overview type, no sources field).
INDEX_PAGE = textwrap.dedent(
    """\
    ---
    title: demo — wiki index
    type: overview
    project: demo
    tags: [index, catalog]
    last_updated: 2026-04-26
    ---

    # demo — wiki index

    ## Top-level
    - [current_state.md](current_state.md)
    """
)

#: Overview page with explicit empty sources block (SCHEMA.md §3 — sources
#: is optional for overview type; this fixture verifies parse_sources
#: returns empty list rather than silently failing).
OVERVIEW_EMPTY_SOURCES = textwrap.dedent(
    """\
    ---
    title: demo — current state
    type: overview
    project: demo
    sources: []
    tags: [overview, snapshot]
    last_updated: 2026-04-26
    ---

    # demo — current state

    No sources ingested yet.
    """
)

#: Frontmatter ending at EOF (no trailing newline after closing `---`).
#: Some editors / scripts strip trailing newlines; the parser must still
#: recognize the frontmatter. Codex round-1 phase-2 LOW.
FRONTMATTER_AT_EOF = (
    "---\n"
    "title: Source — eof.md\n"
    "type: source-summary\n"
    "project: demo\n"
    "sources:\n"
    "  - path: raw/demo/eof.md\n"
    "    source_mtime: 2026-04-26\n"
    "    ingested: 2026-04-26\n"
    "last_updated: 2026-04-26\n"
    "---"  # NOTE: no trailing newline. Body absent.
)


# ─── extract_frontmatter ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,fixture",
    [
        ("minimal_source_summary", MINIMAL_SOURCE_SUMMARY),
        ("self_superseded", SELF_SUPERSEDED_SUMMARY),
        ("removed_upstream", REMOVED_UPSTREAM_SUMMARY),
        ("quoted_path", QUOTED_PATH_SUMMARY),
        ("entity_multi_source", ENTITY_MULTI_SOURCE),
        ("index_page", INDEX_PAGE),
        ("overview_empty_sources", OVERVIEW_EMPTY_SOURCES),
        ("frontmatter_at_eof", FRONTMATTER_AT_EOF),
    ],
)
def test_extract_frontmatter_parses_every_canonical_fixture(
    name: str, fixture: str
) -> None:
    """Every page shape SCHEMA.md documents must yield a non-None
    frontmatter block."""
    fm = extract_frontmatter(fixture)
    assert fm is not None, f"{name}: extract_frontmatter returned None"
    # Every fixture has these required fields per SCHEMA.md §3
    assert "title:" in fm
    assert "type:" in fm
    assert "project:" in fm
    assert "last_updated:" in fm


# ─── parse_sources ──────────────────────────────────────────────────────────


def test_parse_minimal_summary_yields_one_source() -> None:
    fm = extract_frontmatter(MINIMAL_SOURCE_SUMMARY)
    assert fm is not None
    assert parse_sources(fm) == [("raw/demo/foo.md", "2026-04-26")]


def test_parse_self_superseded_uses_current_mtime() -> None:
    """previous_mtimes is for history; the current mtime in `sources:` is
    what parse_sources reports."""
    fm = extract_frontmatter(SELF_SUPERSEDED_SUMMARY)
    assert fm is not None
    sources = parse_sources(fm)
    assert sources == [("raw/demo/foo.md", "2026-04-26")]


def test_parse_removed_upstream_still_yields_source() -> None:
    """A removed-upstream summary still cites its source; the marker is in
    frontmatter, not in `sources:`."""
    fm = extract_frontmatter(REMOVED_UPSTREAM_SUMMARY)
    assert fm is not None
    assert parse_sources(fm) == [("raw/demo/foo.md", "2026-04-26")]


def test_parse_quoted_path_with_spaces() -> None:
    """SCHEMA.md §3: quoted paths must parse. Codex round-1 phase-1 L2."""
    fm = extract_frontmatter(QUOTED_PATH_SUMMARY)
    assert fm is not None
    assert parse_sources(fm) == [("raw/demo/foo bar.md", "2026-04-26")]


def test_parse_entity_multi_source() -> None:
    """Entity pages may cite many sources — parser yields all of them."""
    fm = extract_frontmatter(ENTITY_MULTI_SOURCE)
    assert fm is not None
    sources = parse_sources(fm)
    assert len(sources) == 3
    paths = {p for p, _ in sources}
    assert paths == {
        "raw/demo/docs/architecture.md",
        "raw/demo/docs/runbook.md",
        "raw/demo/CLAUDE.md",
    }


def test_parse_index_page_yields_no_sources() -> None:
    """Bookkeeping pages (overview type) have no `sources:` block — parse
    returns an empty list, not an error."""
    fm = extract_frontmatter(INDEX_PAGE)
    assert fm is not None
    assert parse_sources(fm) == []


def test_parse_overview_with_explicit_empty_sources_list() -> None:
    """SCHEMA.md §3: overview pages may include `sources: []` explicitly
    (e.g. wiki_current_state.md template at bootstrap time before any
    sources have been ingested). Parser must return an empty list, not
    fail. Codex round-1 phase-2 fixture addition."""
    fm = extract_frontmatter(OVERVIEW_EMPTY_SOURCES)
    assert fm is not None
    assert parse_sources(fm) == []


def test_parse_frontmatter_at_eof() -> None:
    """A page whose frontmatter closes at end-of-file (no trailing newline
    after the closing `---`) must still parse. Old regex required a
    trailing newline and silently misclassified such pages as "no
    frontmatter", triggering false NEW/DELETED behavior. Codex round-1
    phase-2 LOW fixture addition."""
    fm = extract_frontmatter(FRONTMATTER_AT_EOF)
    assert fm is not None
    assert "title: Source — eof.md" in fm
    assert parse_sources(fm) == [("raw/demo/eof.md", "2026-04-26")]


# ─── build_source_index against on-disk fixtures ───────────────────────────


def test_build_source_index_against_canonical_fixtures(tmp_path: Path) -> None:
    """End-to-end: write the canonical fixtures into a fake wiki/sources/
    dir tree, then verify build_source_index indexes every source path."""
    sources_dir = tmp_path / "wiki" / "demo" / "sources"
    (sources_dir / "docs").mkdir(parents=True)
    (sources_dir / "minimal.md").write_text(MINIMAL_SOURCE_SUMMARY)
    (sources_dir / "docs" / "self_superseded.md").write_text(SELF_SUPERSEDED_SUMMARY)
    (sources_dir / "removed.md").write_text(REMOVED_UPSTREAM_SUMMARY)
    (sources_dir / "quoted.md").write_text(QUOTED_PATH_SUMMARY)

    idx = build_source_index(sources_dir, "raw/demo")

    # Five summaries citing different paths — but two of them cite "foo.md"
    # in different fixtures. Last-write-wins per SCHEMA convention. Verify
    # that each cited path is indexed.
    cited_paths = {
        "raw/demo/foo.md",  # from minimal/self_superseded/removed (last wins)
        "raw/demo/foo bar.md",  # from quoted
    }
    assert set(idx.keys()) == cited_paths


# ─── invariants from SCHEMA.md §3 ──────────────────────────────────────────


def test_source_summary_must_have_exactly_one_source() -> None:
    """SCHEMA.md §3: source-summary pages cite exactly one source.
    The parser is lenient (yields whatever it finds), but the schema is
    explicit about the expectation. Document the invariant here so future
    schema-conformance tests can lean on it."""
    fm = extract_frontmatter(MINIMAL_SOURCE_SUMMARY)
    assert fm is not None
    assert len(parse_sources(fm)) == 1


def test_overview_pages_may_omit_sources_block() -> None:
    """SCHEMA.md §3: only source-summary pages REQUIRE `sources`. Overview
    pages (index, log, _candidates) may omit it entirely."""
    fm = extract_frontmatter(INDEX_PAGE)
    assert fm is not None
    assert "sources:" not in fm
    assert parse_sources(fm) == []
