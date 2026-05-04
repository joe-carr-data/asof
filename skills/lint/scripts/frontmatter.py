"""Frontmatter parser for wiki pages — stdlib-only, schema-aware.

Wiki pages start with a YAML-fenced frontmatter block per SCHEMA.md §3:

    ---
    title: Some Title
    type: source-summary
    project: myproj
    last_updated: 2026-04-26
    sources:
      - path: raw/myproj/foo.md
        source_mtime: 2026-04-22
        ingested: 2026-04-23
    tags: [a, b]
    removed_upstream: 2026-04-29       # optional
    previous_mtimes:                    # optional list of dates
      - 2026-03-10
    ---

We don't pull in PyYAML (asof is stdlib-only). This parser handles the
exact shape SCHEMA documents:
    - top-level scalar lines: `key: value` (string, ISO date, list-inline)
    - top-level list-inline: `tags: [a, b]`
    - top-level list-block: `previous_mtimes:` followed by `  - date` lines
    - `sources:` list-of-dicts with path/source_mtime/ingested keys

It is deliberately strict about the schema-required structure but tolerant
of extra unrecognized keys (forward-compat). On structural errors (missing
closing fence, malformed list block) it returns a partial parse and lets
the lint check itself flag the issue.

Public API:
    parse_page(text: str) -> tuple[dict, str, int | None]
        → (frontmatter_dict, body, line_of_open_fence_or_None_if_no_fm)
    line_of_field(text: str, field: str) -> int | None
        → 1-indexed line where `<field>:` first appears in the frontmatter,
          or None if not present.
"""

from __future__ import annotations

import re
from typing import Any

# Open-and-close fence pattern. Frontmatter is delimited by `---` lines at
# the very start of the file. `\Z` ensures we only match at the start.
_FRONTMATTER_RE = re.compile(
    r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL
)

# A top-level scalar key: line. Allows quoted or unquoted values.
_SCALAR_LINE = re.compile(
    r"^(?P<indent>)(?P<key>[A-Za-z_][A-Za-z0-9_]*):\s*(?P<value>.*)$"
)

# Inline list `[a, b, c]`.
_INLINE_LIST = re.compile(r"^\[\s*(.*?)\s*\]$")


def parse_page(text: str) -> tuple[dict[str, Any], str, int | None]:
    """Parse a wiki page into (frontmatter_dict, body, fm_open_line).

    `fm_open_line` is 1 if the page starts with `---\n`, or None when
    there's no frontmatter at all (the caller's frontmatter-validity check
    fires the appropriate error).

    Returns an empty `{}` for the frontmatter dict on parse failure /
    missing frontmatter — the body is then the entire input.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return ({}, text, None)
    fm_body = m.group(1)
    page_body = m.group(2)
    parsed = _parse_frontmatter_body(fm_body)
    return (parsed, page_body, 1)


def _parse_frontmatter_body(fm: str) -> dict[str, Any]:
    """Parse the body between the `---` fences.

    Handles top-level keys, inline lists, list-blocks (one indented item
    per line for scalars), and the `sources:` list-of-dicts block.
    """
    out: dict[str, Any] = {}
    lines = fm.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # Skip blank lines and comments.
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = _SCALAR_LINE.match(line)
        if not m:
            # Unrecognized top-level shape; skip with no partial corruption.
            i += 1
            continue
        key = m.group("key")
        value = m.group("value").rstrip()

        if value:
            # Inline value (scalar or inline list).
            out[key] = _parse_inline_value(value)
            i += 1
        else:
            # Block: collect indented children below this line.
            block_lines, consumed = _collect_block(lines, i + 1)
            if key == "sources":
                out[key] = _parse_sources_block(block_lines)
            elif block_lines:
                # Generic list-of-scalars block (e.g. previous_mtimes:).
                out[key] = _parse_scalar_list_block(block_lines)
            else:
                # Empty block — treat as empty list.
                out[key] = []
            i += 1 + consumed
    return out


def _collect_block(lines: list[str], start: int) -> tuple[list[str], int]:
    """Collect contiguous indented lines starting at `start`.

    Returns (block_lines, count_consumed). Blanks inside the block are
    preserved; the block ends at the next non-indented non-blank line.
    """
    block: list[str] = []
    consumed = 0
    for j in range(start, len(lines)):
        line = lines[j]
        if not line.strip():
            block.append(line)
            consumed += 1
            continue
        if line[0] in (" ", "\t"):
            block.append(line)
            consumed += 1
            continue
        break
    # Trim trailing blanks.
    while block and not block[-1].strip():
        block.pop()
    return (block, consumed)


def _parse_inline_value(value: str) -> Any:
    """Parse a single inline value: inline list, quoted string, or scalar.

    Inline lists use a quote-aware comma splitter so values like
        aliases: ["foo, bar", baz]
    parse as ["foo, bar", "baz"] and not ["foo", "bar", "baz"]
    (Codex round-1 phase-4 MEDIUM).
    """
    list_m = _INLINE_LIST.match(value)
    if list_m:
        body = list_m.group(1)
        if not body.strip():
            return []
        return [_strip_quotes(item.strip()) for item in _split_inline_list(body)]
    return _strip_quotes(value)


def _split_inline_list(body: str) -> list[str]:
    """Quote-aware comma split. Tracks single + double quote state so commas
    inside quoted strings are NOT separators. Mismatched quotes fall back
    to naive split (the upstream finding will surface the malformed shape)."""
    parts: list[str] = []
    cur: list[str] = []
    in_single = False
    in_double = False
    for ch in body:
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch)
        elif ch == "," and not in_single and not in_double:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _strip_quotes(value: str) -> str:
    """Strip matching surrounding single or double quotes if present."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_scalar_list_block(block_lines: list[str]) -> list[str]:
    """Parse a block like:
        - 2026-03-10
        - 2026-04-12
    into ["2026-03-10", "2026-04-12"].

    Codex round-1 phase-4 MEDIUM: requires a SPACE after the dash, so
    `-foo` (without space) is NOT treated as a list item. YAML requires
    `- foo` syntax; accepting `-foo` was silently swallowing malformed
    frontmatter. Pure scalar values like `- 2026-03-10` and the bare
    `-` (empty list item, allowed by YAML for null) are still accepted.
    """
    out: list[str] = []
    for line in block_lines:
        stripped = line.strip()
        if stripped == "-":
            # Bare `-` → empty/null list item (rare but valid YAML).
            out.append("")
        elif stripped.startswith("- "):
            out.append(_strip_quotes(stripped[2:].strip()))
        # Anything else (`-foo`, `*foo`, plain text) is silently dropped.
        # The lint frontmatter check will surface "missing field" if a
        # required block ends up empty as a result.
    return out


def _parse_sources_block(block_lines: list[str]) -> list[dict[str, Any]]:
    """Parse a `sources:` block — a list of dicts.

        sources:
          - path: raw/x.md
            source_mtime: 2026-04-22
            ingested: 2026-04-23
            previous_mtimes:
              - 2026-03-10
          - path: 'raw/y with space.md'
            source_mtime: 2026-04-25

    Each `- ` line at the FIRST indent level starts a new source dict.
    A `- ` at a deeper indent belongs to the current source's pending
    sub-block (e.g. `previous_mtimes:`). Subsequent indented `key: value`
    lines belong to the most recent source dict.
    """
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_block_key: str | None = None
    pending_block_lines: list[str] = []
    # Indent of the first source-entry `- ` line; subsequent `- ` lines
    # at this exact indent are new entries; deeper ones are sub-list items.
    entry_indent: int | None = None

    def _flush_block_to_current() -> None:
        nonlocal pending_block_key, pending_block_lines
        if current is None or pending_block_key is None:
            pending_block_key = None
            pending_block_lines = []
            return
        current[pending_block_key] = _parse_scalar_list_block(pending_block_lines)
        pending_block_key = None
        pending_block_lines = []

    for line in block_lines:
        if not line.strip():
            if pending_block_key is not None:
                pending_block_lines.append(line)
            continue

        # Compute leading-whitespace count — distinguishes entry-level
        # `- ` from sub-list `- `.
        indent = len(line) - len(line.lstrip())
        stripped = line.lstrip()

        is_dash = stripped.startswith("- ") or stripped == "-"

        # Decide: entry-level `- ` vs sub-list `- `.
        if is_dash and (entry_indent is None or indent <= entry_indent):
            # New source entry.
            _flush_block_to_current()
            entry_indent = indent if entry_indent is None else entry_indent
            current = {}
            entries.append(current)
            after_dash = stripped[2:].lstrip() if stripped.startswith("- ") else ""
            kv_m = _SCALAR_LINE.match(after_dash) if after_dash else None
            if kv_m:
                k = kv_m.group("key")
                v = kv_m.group("value").rstrip()
                if v:
                    current[k] = _parse_inline_value(v)
                else:
                    pending_block_key = k
            continue

        # Sub-list `- ` (deeper indent than entry_indent) — append to pending block.
        if is_dash and pending_block_key is not None:
            pending_block_lines.append(line)
            continue

        # Continuation of current entry — `key: value` at deeper indent.
        kv_m = _SCALAR_LINE.match(stripped)
        if kv_m and current is not None:
            _flush_block_to_current()
            k = kv_m.group("key")
            v = kv_m.group("value").rstrip()
            if v:
                current[k] = _parse_inline_value(v)
            else:
                pending_block_key = k
            continue

        # Continuation of pending block — generic indented line.
        if pending_block_key is not None:
            pending_block_lines.append(line)

    _flush_block_to_current()
    return entries


def line_of_field(text: str, field: str) -> int | None:
    """Return the 1-indexed line where `<field>:` first appears in the
    frontmatter, or None if the page has no frontmatter or the field is
    absent. Used to attach line numbers to lint findings.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    fm_body = m.group(1)
    pattern = re.compile(rf"^\s*{re.escape(field)}:\s*", re.MULTILINE)
    fm_match = pattern.search(fm_body)
    if not fm_match:
        return None
    # +1 because frontmatter starts at line 2 (line 1 = opening `---`).
    return fm_body[: fm_match.start()].count("\n") + 2
