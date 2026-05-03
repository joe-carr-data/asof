"""Reporting layer for asof:sync.

Two outputs:
    1. **Human report**: structured text for the agent + the user. Lists
       NEW/MODIFIED/DELETED/skipped per project, plus rsync stats.
    2. **JSON last-sync file**: written atomically to
       `<wiki_dir>/.last-sync/<project>.json` so tests / lint / future
       skills can consume the same data programmatically.

Both consume `DeltaReport` (from delta.py) + `RsyncResult` (from
rsync_runner.py) — pure transformation, no I/O policy decisions.

Stdlib only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import WikiConfig
from delta import DeltaReport
from rsync_runner import RsyncResult
from utils import SKILL_VERSION, atomic_write_json

#: Display only the first N entries of each delta category in the human
#: report unless --summary-only forces just counts. 60 chosen empirically
#: (fits a typical terminal page; longer reports get noisy).
DEFAULT_LIST_LIMIT: int = 60


# ─── JSON serialization ────────────────────────────────────────────────────


def serialize_to_dict(
    delta: DeltaReport,
    rsync: RsyncResult | None,
    *,
    skill_version: str = SKILL_VERSION,
    schema_version: str = "1.0",
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Build the JSON-serializable dict for `.last-sync/<project>.json`.

    `rsync=None` is allowed (e.g. `--dry-run` paths that skipped rsync)
    so the serializer keeps a stable shape with explicit `null`.

    Args:
        delta: the DeltaReport for this project.
        rsync: the RsyncResult, or None if rsync didn't run.
        skill_version: override for testing (defaults to runtime SKILL_VERSION).
        schema_version: report-format version (separate from wiki schema_version).
        timestamp: override for deterministic tests; defaults to now-UTC.
    """
    ts = (timestamp or datetime.now(timezone.utc)).isoformat()
    rsync_block: dict[str, Any] | None
    if rsync is None:
        rsync_block = None
    else:
        rsync_block = {
            "succeeded": rsync.succeeded,
            "return_code": rsync.return_code,
            "transferred": rsync.transferred,
            "deleted": rsync.deleted,
            "dry_run": rsync.dry_run,
        }
    return {
        "schema_version": schema_version,
        "asof_version": skill_version,
        "project_name": delta.project_name,
        "raw_subdir": delta.raw_subdir,
        "wiki_subdir": delta.wiki_subdir,
        "rsync": rsync_block,
        "deltas": {
            "new": [
                {"rel_path": n.rel_path, "mtime": n.mtime} for n in delta.new
            ],
            "modified": [
                {
                    "rel_path": m.rel_path,
                    "old_mtime": m.old_mtime,
                    "new_mtime": m.new_mtime,
                }
                for m in delta.modified
            ],
            "deleted": [
                {"raw_path": d.raw_path, "summary_path": d.summary_path}
                for d in delta.deleted
            ],
            "skipped_symlinks": [
                {"rel_path": s.rel_path, "target": s.target}
                for s in delta.skipped_symlinks
            ],
        },
        "totals": {
            "new": len(delta.new),
            "modified": len(delta.modified),
            "deleted": len(delta.deleted),
            "skipped_symlinks": len(delta.skipped_symlinks),
            "total_changes": delta.total_changes,
        },
        "timestamp": ts,
    }


def write_last_sync(
    config: WikiConfig,
    delta: DeltaReport,
    rsync: RsyncResult | None,
    *,
    timestamp: datetime | None = None,
) -> Path:
    """Atomically write `<wiki_dir>/.last-sync/<project>.json` and return its path.

    Per-project file (PLAN.md round-2 fix): a global `.last-sync.json` would
    let one project's run clobber another. Each project gets its own report.
    """
    payload = serialize_to_dict(delta, rsync, timestamp=timestamp)
    target = config.last_sync_dir / f"{delta.project_name}.json"
    atomic_write_json(target, payload)
    return target


# ─── human report ──────────────────────────────────────────────────────────


def render_human_report(
    delta: DeltaReport,
    rsync: RsyncResult | None,
    *,
    summary_only: bool = False,
    list_limit: int = DEFAULT_LIST_LIMIT,
) -> str:
    """Render a per-project text report for the agent / user.

    Layout matches the original brain-sync output (so existing users see
    familiar text). Safe to print directly — no terminal escape sequences,
    plain ASCII + minor box-drawing if you squint.

    Args:
        delta: the DeltaReport.
        rsync: the RsyncResult, or None for dry-run / no-rsync paths.
        summary_only: if True, suppress per-file listings (just print counts).
        list_limit: cap per-category lines; remainder collapsed to "... and N more".
    """
    lines: list[str] = []
    lines.append(f"=== project: {delta.project_name} ===")
    lines.append(f"raw:    {delta.raw_subdir}")
    lines.append(f"wiki:   {delta.wiki_subdir}")
    if rsync is not None:
        suffix = " (dry-run)" if rsync.dry_run else ""
        lines.append(f"rsync:  exit={rsync.return_code}{suffix}")
        lines.append(
            f"        {rsync.transferred} .md files transferred, "
            f"{rsync.deleted} deleted"
        )
    else:
        lines.append("rsync:  (skipped)")
    lines.append("")

    lines.extend(
        _render_section(
            "NEW",
            len(delta.new),
            (
                f"  {delta.raw_subdir}/{n.rel_path}  (mtime: {n.mtime})"
                for n in delta.new
            ),
            summary_only=summary_only,
            list_limit=list_limit,
        )
    )
    lines.extend(
        _render_section(
            "MODIFIED",
            len(delta.modified),
            (
                f"  {delta.raw_subdir}/{m.rel_path}  "
                f"(was {m.old_mtime}, now {m.new_mtime})"
                for m in delta.modified
            ),
            summary_only=summary_only,
            list_limit=list_limit,
        )
    )
    lines.extend(
        _render_section(
            "DELETED",
            len(delta.deleted),
            (
                f"  {d.raw_path}  (no longer in raw/, source-summary still in wiki)"
                for d in delta.deleted
            ),
            summary_only=summary_only,
            list_limit=list_limit,
        )
    )

    if delta.skipped_symlinks:
        lines.append(
            f"SYMLINKS skipped ({len(delta.skipped_symlinks)}, "
            f"treated as aliases of their targets):"
        )
        if not summary_only:
            for s in list(delta.skipped_symlinks)[:list_limit]:
                lines.append(f"  {delta.raw_subdir}/{s.rel_path}")
            extra = len(delta.skipped_symlinks) - list_limit
            if extra > 0:
                lines.append(f"  ... and {extra} more")
        lines.append("")

    return "\n".join(lines)


def _render_section(
    label: str,
    count: int,
    item_iter: Any,
    *,
    summary_only: bool,
    list_limit: int,
) -> list[str]:
    """Render one delta category (NEW / MODIFIED / DELETED) with truncation."""
    out: list[str] = [f"{label} ({count}):"]
    if summary_only or count == 0:
        out.append("")
        return out
    items = list(item_iter)
    for line in items[:list_limit]:
        out.append(line)
    extra = len(items) - list_limit
    if extra > 0:
        out.append(f"  ... and {extra} more")
    out.append("")
    return out


# ─── multi-project summary ────────────────────────────────────────────────


def render_run_summary(
    reports: list[tuple[DeltaReport, RsyncResult | None]],
) -> str:
    """Render the trailing summary across all projects synced this run."""
    total_new = sum(len(d.new) for d, _ in reports)
    total_modified = sum(len(d.modified) for d, _ in reports)
    total_deleted = sum(len(d.deleted) for d, _ in reports)
    total_changes = total_new + total_modified + total_deleted
    transferred = sum(r.transferred for _, r in reports if r is not None)
    deleted_files = sum(r.deleted for _, r in reports if r is not None)

    lines = ["=== summary ==="]
    lines.append(f"projects synced: {len(reports)}")
    lines.append(
        f"rsync:           {transferred} transferred, {deleted_files} deleted"
    )
    lines.append(
        f"deltas:          NEW={total_new} MODIFIED={total_modified} "
        f"DELETED={total_deleted} (total={total_changes})"
    )
    if total_changes == 0:
        lines.append("wiki is up to date.")
    return "\n".join(lines)
