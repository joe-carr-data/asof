"""Tests for skills/lint/scripts/frontmatter.py — the stdlib YAML-shape parser.

Covers SCHEMA.md §3 frontmatter shapes and edge cases the lint checks rely on.
"""

from __future__ import annotations

from frontmatter import line_of_field, parse_page


def test_parse_page_no_frontmatter() -> None:
    """Page without `---` fence returns ({}, body, None)."""
    fm, body, line = parse_page("# Just a heading\n")
    assert fm == {}
    assert body == "# Just a heading\n"
    assert line is None


def test_parse_page_minimal_required_fields() -> None:
    text = "---\ntitle: T\ntype: entity\nproject: p\nlast_updated: 2026-04-26\n---\n\nbody\n"
    fm, body, line = parse_page(text)
    assert fm == {
        "title": "T",
        "type": "entity",
        "project": "p",
        "last_updated": "2026-04-26",
    }
    assert body.strip() == "body"
    assert line == 1


def test_parse_page_inline_list_tags() -> None:
    text = "---\ntitle: T\ntype: entity\ntags: [data, ml, time-series]\n---\n"
    fm, _, _ = parse_page(text)
    assert fm["tags"] == ["data", "ml", "time-series"]


def test_parse_page_quoted_inline_list() -> None:
    text = "---\ntags: ['has space', \"has quotes\", plain]\n---\n"
    fm, _, _ = parse_page(text)
    assert fm["tags"] == ["has space", "has quotes", "plain"]


def test_parse_page_block_scalar_list() -> None:
    text = (
        "---\n"
        "previous_mtimes:\n"
        "  - 2026-03-10\n"
        "  - 2026-02-01\n"
        "---\n"
    )
    fm, _, _ = parse_page(text)
    assert fm["previous_mtimes"] == ["2026-03-10", "2026-02-01"]


def test_parse_page_sources_block_simple() -> None:
    text = (
        "---\n"
        "sources:\n"
        "  - path: raw/myproj/foo.md\n"
        "    source_mtime: 2026-04-22\n"
        "    ingested: 2026-04-23\n"
        "---\n"
    )
    fm, _, _ = parse_page(text)
    assert len(fm["sources"]) == 1
    assert fm["sources"][0]["path"] == "raw/myproj/foo.md"
    assert fm["sources"][0]["source_mtime"] == "2026-04-22"


def test_parse_page_sources_block_two_entries() -> None:
    text = (
        "---\n"
        "sources:\n"
        "  - path: raw/a.md\n"
        "    source_mtime: 2026-04-22\n"
        "  - path: raw/b.md\n"
        "    source_mtime: 2026-04-25\n"
        "---\n"
    )
    fm, _, _ = parse_page(text)
    assert len(fm["sources"]) == 2
    assert fm["sources"][0]["path"] == "raw/a.md"
    assert fm["sources"][1]["path"] == "raw/b.md"


def test_parse_page_sources_with_quoted_path() -> None:
    text = (
        "---\n"
        "sources:\n"
        "  - path: 'raw/has space.md'\n"
        "    source_mtime: 2026-04-22\n"
        "  - path: \"raw/double quoted.md\"\n"
        "    source_mtime: 2026-04-25\n"
        "---\n"
    )
    fm, _, _ = parse_page(text)
    assert fm["sources"][0]["path"] == "raw/has space.md"
    assert fm["sources"][1]["path"] == "raw/double quoted.md"


def test_parse_page_sources_with_nested_previous_mtimes() -> None:
    """A `- ` at deeper indent must NOT be parsed as a new source entry —
    it's a sub-list item under the current entry's pending block."""
    text = (
        "---\n"
        "sources:\n"
        "  - path: raw/a.md\n"
        "    source_mtime: 2026-04-25\n"
        "    previous_mtimes:\n"
        "      - 2026-03-10\n"
        "      - 2026-02-01\n"
        "  - path: raw/b.md\n"
        "    source_mtime: 2026-04-26\n"
        "---\n"
    )
    fm, _, _ = parse_page(text)
    assert len(fm["sources"]) == 2  # the deeper - lines must not split entries
    assert fm["sources"][0]["previous_mtimes"] == ["2026-03-10", "2026-02-01"]
    assert fm["sources"][1]["path"] == "raw/b.md"


def test_parse_page_tolerates_extra_keys() -> None:
    """Forward-compat: unknown top-level keys survive parsing."""
    text = (
        "---\n"
        "title: T\n"
        "future_key: future_value\n"
        "another_future_key: 42\n"
        "---\n"
    )
    fm, _, _ = parse_page(text)
    assert fm["future_key"] == "future_value"
    assert fm["another_future_key"] == "42"


def test_parse_page_unclosed_fence_returns_no_frontmatter() -> None:
    """Page that opens with `---` but has no closing fence returns no
    frontmatter (lint then fires the frontmatter check)."""
    text = "---\ntitle: T\ntype: entity\n\nNo closing fence."
    fm, _, line = parse_page(text)
    assert fm == {}
    assert line is None


def test_line_of_field_returns_correct_line() -> None:
    text = (
        "---\n"           # line 1
        "title: T\n"      # line 2
        "type: entity\n"  # line 3
        "project: p\n"    # line 4
        "last_updated: 2026-04-26\n"  # line 5
        "---\n"
    )
    assert line_of_field(text, "title") == 2
    assert line_of_field(text, "type") == 3
    assert line_of_field(text, "project") == 4
    assert line_of_field(text, "last_updated") == 5


def test_line_of_field_returns_none_for_missing_field() -> None:
    text = "---\ntitle: T\n---\n"
    assert line_of_field(text, "nonexistent") is None


def test_line_of_field_returns_none_for_no_frontmatter() -> None:
    assert line_of_field("# just a body\n", "title") is None
