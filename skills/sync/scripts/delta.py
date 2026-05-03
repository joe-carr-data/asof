"""Delta detection for asof:sync.

Walks `<wiki_dir>/raw/<project>/` and compares each `*.md` file's mtime
against the `source_mtime` recorded in the corresponding source-summary's
frontmatter under `<wiki_dir>/wiki/<project>/sources/`.

Produces a structured `DeltaReport` with NEW / MODIFIED / DELETED records.
The agent re-ingests deltas using the rules in `references/INGEST_PROCEDURE.md`
(phase 2).

Why this is its own module:
    - Frontmatter parsing has subtle rules (nested `sources:` block, mixed
      YAML+markdown). Isolating it makes it testable in isolation.
    - The `rglob` fix (Codex review prep — original brain-sync used `glob`
      which missed nested source-summary directories) lives here and only
      here.
    - Symlink policy (default: skip with `--safe-links` semantics; opt-in
      `--copy-links` to resolve) is enforced here so it can't be bypassed
      by other call sites.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from config import ProjectConfig
from utils import get_mtime_iso

# ─── data model ─────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class NewRaw:
    """A raw file with no source-summary in the wiki yet."""

    rel_path: str  # relative to raw_subdir, POSIX form
    mtime: str  # YYYY-MM-DD


@dataclasses.dataclass(frozen=True)
class ModifiedRaw:
    """A raw file whose mtime differs from the recorded source_mtime."""

    rel_path: str
    old_mtime: str  # what the wiki summary recorded
    new_mtime: str  # current file mtime


@dataclasses.dataclass(frozen=True)
class DeletedSummary:
    """A source-summary whose cited raw file no longer exists."""

    raw_path: str  # path the summary cited (e.g., "raw/foo/docs/x.md")
    summary_path: str  # absolute path of the summary in the wiki


@dataclasses.dataclass(frozen=True)
class SkippedSymlink:
    """A symlink in raw/ that we skipped (default --safe-links behavior)."""

    rel_path: str
    target: str  # symlink target (may be absolute or relative)


@dataclasses.dataclass(frozen=True)
class DeltaReport:
    """Aggregated delta report for a single project."""

    project_name: str
    raw_subdir: str
    wiki_subdir: str
    new: tuple[NewRaw, ...]
    modified: tuple[ModifiedRaw, ...]
    deleted: tuple[DeletedSummary, ...]
    skipped_symlinks: tuple[SkippedSymlink, ...]

    @property
    def total_changes(self) -> int:
        return len(self.new) + len(self.modified) + len(self.deleted)

    @property
    def is_empty(self) -> bool:
        return self.total_changes == 0


class StrictMtimeError(ValueError):
    """Raised in `--strict-mtime` mode when an mtime regression is detected."""


# ─── frontmatter parsing ────────────────────────────────────────────────────
#
# Wiki source-summaries have YAML frontmatter at the top, fenced by `---`:
#
#     ---
#     title: ...
#     sources:
#       - path: raw/traddea/docs/x.md
#         source_mtime: 2026-04-26
#         ingested: 2026-04-26
#     ---
#
# We don't pull in PyYAML (stdlib-only). The grammar we care about is small
# enough to parse with two regexes: one for the frontmatter fence, one for
# the `sources:` block within it.

#: Frontmatter fence — accepts a closing `---` followed by either a
#: newline OR end-of-file. Old form `---\s*\n` rejected pages whose
#: frontmatter ended at EOF (no trailing newline), causing them to be
#: silently misclassified as "no frontmatter" and triggering false
#: NEW/DELETED behavior. Codex round-1 phase-2 LOW.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_SOURCES_BLOCK_RE = re.compile(
    r"^sources:\s*\n((?:[ \t]+.*\n?)+)", re.MULTILINE
)


def extract_frontmatter(text: str) -> str | None:
    """Return the raw frontmatter body (between the `---` fences), or None."""
    m = _FRONTMATTER_RE.match(text)
    return m.group(1) if m else None


def parse_sources(frontmatter: str) -> list[tuple[str, str]]:
    """Yield (path, source_mtime) tuples from a `sources:` block.

    Tolerates extra fields per entry (ingested, previous_mtimes, etc.) —
    we only care about path + source_mtime. Entries missing either field
    are silently skipped (the caller treats those as orphan summaries; lint
    later flags them via the "missing source_mtime" check).
    """
    m = _SOURCES_BLOCK_RE.search(frontmatter)
    if not m:
        return []
    block = m.group(1)
    # Split on lines beginning with a list-item dash. Use a regex that allows
    # any leading whitespace so 2-space and 4-space indentation both work.
    entries = re.split(r"\n(?=[ \t]+-\s)", block)
    out: list[tuple[str, str]] = []
    # `path:` may be unquoted (foo.md), single-quoted ('path with space.md'),
    # or double-quoted ("path with space.md"). Mtime is always a bare ISO
    # date so a simple \S+ match suffices.
    path_pattern = re.compile(
        r"""-\s*path:\s*(?:"(?P<dq>[^"\n]+)"|'(?P<sq>[^'\n]+)'|(?P<bare>\S+))"""
    )
    for entry in entries:
        path_m = path_pattern.search(entry)
        mtime_m = re.search(r"source_mtime:\s*(\S+)", entry)
        if path_m and mtime_m:
            path = path_m.group("dq") or path_m.group("sq") or path_m.group("bare")
            out.append((path, mtime_m.group(1).strip()))
    return out


# ─── source-summary index ──────────────────────────────────────────────────


def build_source_index(
    wiki_sources_dir: Path, raw_subdir: str
) -> dict[str, tuple[Path, str]]:
    """Map every cited raw path → (summary_path, recorded source_mtime).

    Recursively walks every `*.md` under `wiki_sources_dir` (the rglob fix —
    summaries are organized in a directory tree mirroring source paths, and
    the original brain-sync's non-recursive glob silently missed nested
    summaries, producing 162 false-positive NEW deltas — see PLAN section 14
    `tooling-fix` log entry).

    Filters to paths starting with `raw_subdir` so cross-project summaries
    in shared wikis don't pollute one project's delta detection.

    If multiple summaries cite the same source path, last one wins. (Should
    be impossible in a well-formed wiki; documenting the behavior for safety.)
    """
    index: dict[str, tuple[Path, str]] = {}
    if not wiki_sources_dir.exists():
        return index
    for summary in wiki_sources_dir.rglob("*.md"):
        try:
            text = summary.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = extract_frontmatter(text)
        if fm is None:
            continue
        for path_str, mtime in parse_sources(fm):
            if path_str.startswith(raw_subdir):
                index[path_str] = (summary, mtime)
    return index


# ─── delta detection ────────────────────────────────────────────────────────


def detect_deltas(
    project: ProjectConfig,
    wiki_dir: Path,
    *,
    follow_symlinks: bool = False,
    strict_mtime: bool = False,
) -> DeltaReport:
    """Compute NEW / MODIFIED / DELETED deltas for one project.

    Args:
        project: the project to scan.
        wiki_dir: the wiki root (project's raw/ + wiki/ paths derive from this).
        follow_symlinks: if True (`--copy-links`), include symlinked files
            in the scan. Default False (`--safe-links` semantics): symlinks
            are skipped and reported as `skipped_symlinks`.
        strict_mtime: if True, raise `StrictMtimeError` when a recorded
            `source_mtime` is *newer* than the current file mtime (a
            regression — usually a bookkeeping bug in the wiki). Off by
            default since rare; on-by-flag for CI / paranoid runs.

    Returns:
        `DeltaReport` with frozen tuples of records.
    """
    raw_root = project.raw_path(wiki_dir)
    wiki_sources = project.wiki_path(wiki_dir) / "sources"

    index = build_source_index(wiki_sources, project.raw_subdir)

    new_files: list[NewRaw] = []
    modified_files: list[ModifiedRaw] = []
    skipped: list[SkippedSymlink] = []
    seen_keys: set[str] = set()

    if raw_root.exists():
        for raw_file in sorted(raw_root.rglob("*.md")):
            if raw_file.is_symlink() and not follow_symlinks:
                rel = raw_file.relative_to(raw_root)
                target = str(raw_file.readlink())
                skipped.append(SkippedSymlink(rel_path=rel.as_posix(), target=target))
                continue
            if not raw_file.is_file():
                continue
            rel = raw_file.relative_to(raw_root)
            key = f"{project.raw_subdir}/{rel.as_posix()}"
            seen_keys.add(key)
            current = get_mtime_iso(raw_file)
            if key not in index:
                new_files.append(NewRaw(rel_path=rel.as_posix(), mtime=current))
                continue
            recorded = index[key][1]
            if recorded == current:
                continue
            # Detect regressions in strict mode.
            if strict_mtime and recorded > current:
                raise StrictMtimeError(
                    f"mtime regression: {key!r} recorded {recorded}, "
                    f"current {current} (older). Wiki summary was likely "
                    f"ingested with the wrong mtime — investigate before "
                    f"re-syncing."
                )
            modified_files.append(
                ModifiedRaw(
                    rel_path=rel.as_posix(),
                    old_mtime=recorded,
                    new_mtime=current,
                )
            )

    deleted = [
        DeletedSummary(raw_path=p, summary_path=str(index[p][0]))
        for p in sorted(index)
        if p not in seen_keys
    ]

    return DeltaReport(
        project_name=project.name,
        raw_subdir=project.raw_subdir,
        wiki_subdir=project.wiki_subdir,
        new=tuple(new_files),
        modified=tuple(modified_files),
        deleted=tuple(deleted),
        skipped_symlinks=tuple(skipped),
    )
