"""Tests for skills/sync/scripts/report.py.

JSON serialization, atomic last-sync write, human report rendering with
list truncation + summary-only mode.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config import ProjectConfig, WikiConfig, load_wiki_config
from delta import (
    DeletedSummary,
    DeltaReport,
    ModifiedRaw,
    NewRaw,
    SkippedSymlink,
)
from report import (
    DEFAULT_LIST_LIMIT,
    render_human_report,
    render_run_summary,
    serialize_to_dict,
    write_last_sync,
)
from rsync_runner import RsyncResult

# ─── fixtures / builders ────────────────────────────────────────────────────


def _make_delta(
    *,
    project_name: str = "demo",
    new: int = 0,
    modified: int = 0,
    deleted: int = 0,
    skipped: int = 0,
) -> DeltaReport:
    return DeltaReport(
        project_name=project_name,
        raw_subdir=f"raw/{project_name}",
        wiki_subdir=f"wiki/{project_name}",
        new=tuple(NewRaw(f"new-{i}.md", "2026-04-26") for i in range(new)),
        modified=tuple(
            ModifiedRaw(f"mod-{i}.md", "2025-01-01", "2026-04-26") for i in range(modified)
        ),
        deleted=tuple(
            DeletedSummary(
                f"raw/{project_name}/del-{i}.md",
                f"/wiki/{project_name}/sources/del-{i}.md",
            )
            for i in range(deleted)
        ),
        skipped_symlinks=tuple(
            SkippedSymlink(f"link-{i}.md", "/elsewhere") for i in range(skipped)
        ),
    )


def _make_rsync(*, transferred: int = 0, deleted: int = 0, dry_run: bool = False) -> RsyncResult:
    return RsyncResult(
        project_name="demo",
        return_code=0,
        transferred=transferred,
        deleted=deleted,
        dry_run=dry_run,
        raw_stdout="",
        raw_stderr="",
    )


_FIXED_TIME = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)


# ─── serialize_to_dict ──────────────────────────────────────────────────────


def test_serialize_shape() -> None:
    delta = _make_delta(new=2, modified=1, deleted=1, skipped=1)
    rsync = _make_rsync(transferred=3, deleted=1)
    payload = serialize_to_dict(
        delta,
        rsync,
        skill_version="9.9.9-test",
        timestamp=_FIXED_TIME,
    )

    # Top-level keys
    expected_keys = {
        "schema_version",
        "asof_version",
        "project_name",
        "raw_subdir",
        "wiki_subdir",
        "rsync",
        "deltas",
        "totals",
        "timestamp",
    }
    assert set(payload.keys()) == expected_keys
    assert payload["asof_version"] == "9.9.9-test"
    assert payload["project_name"] == "demo"
    assert payload["timestamp"] == _FIXED_TIME.isoformat()


def test_serialize_rsync_block_shape() -> None:
    delta = _make_delta()
    rsync = _make_rsync(transferred=5, deleted=2, dry_run=True)
    payload = serialize_to_dict(delta, rsync, timestamp=_FIXED_TIME)
    assert payload["rsync"] == {
        "succeeded": True,
        "return_code": 0,
        "transferred": 5,
        "deleted": 2,
        "dry_run": True,
    }


def test_serialize_rsync_none_yields_explicit_null() -> None:
    """When rsync didn't run (e.g. dry-run path), the JSON has explicit null."""
    delta = _make_delta()
    payload = serialize_to_dict(delta, None, timestamp=_FIXED_TIME)
    assert payload["rsync"] is None


def test_serialize_deltas_record_shape() -> None:
    delta = _make_delta(new=1, modified=1, deleted=1, skipped=1)
    payload = serialize_to_dict(delta, None, timestamp=_FIXED_TIME)

    assert payload["deltas"]["new"][0] == {
        "rel_path": "new-0.md",
        "mtime": "2026-04-26",
    }
    assert payload["deltas"]["modified"][0] == {
        "rel_path": "mod-0.md",
        "old_mtime": "2025-01-01",
        "new_mtime": "2026-04-26",
    }
    assert payload["deltas"]["deleted"][0] == {
        "raw_path": "raw/demo/del-0.md",
        "summary_path": "/wiki/demo/sources/del-0.md",
    }
    assert payload["deltas"]["skipped_symlinks"][0] == {
        "rel_path": "link-0.md",
        "target": "/elsewhere",
    }


def test_serialize_totals_match_lists() -> None:
    delta = _make_delta(new=3, modified=2, deleted=4, skipped=1)
    payload = serialize_to_dict(delta, None, timestamp=_FIXED_TIME)
    assert payload["totals"] == {
        "new": 3,
        "modified": 2,
        "deleted": 4,
        "skipped_symlinks": 1,
        "total_changes": 9,  # new+mod+del, NOT counting skipped symlinks
    }


def test_serialize_is_json_round_trippable() -> None:
    delta = _make_delta(new=1, modified=1, deleted=1)
    payload = serialize_to_dict(delta, _make_rsync(), timestamp=_FIXED_TIME)
    serialized = json.dumps(payload)
    restored = json.loads(serialized)
    assert restored == payload


# ─── write_last_sync ────────────────────────────────────────────────────────


def _build_wiki(tmp_path: Path) -> tuple[ProjectConfig, WikiConfig]:
    wiki_dir = tmp_path / "wiki"
    src = tmp_path / "src"
    src.mkdir()
    cfg_data = {
        "wiki_dir": str(wiki_dir),
        "schema_version": "1.0",
        "min_reader_version": "1.0",
        "min_writer_version": "1.0",
        "projects": [
            {
                "name": "demo",
                "source": str(src),
                "raw_subdir": "raw/demo",
                "wiki_subdir": "wiki/demo",
                "excludes": [".asof", ".last-sync"],
            }
        ],
    }
    wiki_dir.mkdir()
    (wiki_dir / ".asof.json").write_text(json.dumps(cfg_data))
    cfg = load_wiki_config(wiki_dir)
    assert isinstance(cfg, WikiConfig)
    return cfg.projects[0], cfg


def test_write_last_sync_creates_per_project_file(tmp_path: Path) -> None:
    proj, cfg = _build_wiki(tmp_path)
    delta = _make_delta(project_name=proj.name, new=2)
    rsync = _make_rsync(transferred=2)
    out = write_last_sync(cfg, delta, rsync, timestamp=_FIXED_TIME)
    assert out == cfg.last_sync_dir / "demo.json"
    assert out.is_file()


def test_write_last_sync_creates_subdir(tmp_path: Path) -> None:
    """The .last-sync/ dir is created on first write."""
    proj, cfg = _build_wiki(tmp_path)
    assert not cfg.last_sync_dir.exists()
    delta = _make_delta(project_name=proj.name)
    write_last_sync(cfg, delta, None, timestamp=_FIXED_TIME)
    assert cfg.last_sync_dir.is_dir()


def test_write_last_sync_atomic_overwrite(tmp_path: Path) -> None:
    """Second write replaces the first (atomic). No temp files remain."""
    proj, cfg = _build_wiki(tmp_path)
    delta1 = _make_delta(project_name=proj.name, new=1)
    delta2 = _make_delta(project_name=proj.name, modified=2)
    write_last_sync(cfg, delta1, None, timestamp=_FIXED_TIME)
    write_last_sync(cfg, delta2, None, timestamp=_FIXED_TIME)
    final = json.loads((cfg.last_sync_dir / "demo.json").read_text())
    assert final["totals"]["new"] == 0
    assert final["totals"]["modified"] == 2
    # No temp files
    leftover = [
        f for f in cfg.last_sync_dir.iterdir() if f.name != "demo.json"
    ]
    assert leftover == []


def test_write_last_sync_per_project_isolation(tmp_path: Path) -> None:
    """Two projects in the same wiki don't clobber each other (round-2 fix)."""
    proj, cfg = _build_wiki(tmp_path)
    delta_a = _make_delta(project_name="alpha", new=1)
    delta_b = _make_delta(project_name="beta", modified=2)
    write_last_sync(cfg, delta_a, None, timestamp=_FIXED_TIME)
    write_last_sync(cfg, delta_b, None, timestamp=_FIXED_TIME)
    files = sorted(p.name for p in cfg.last_sync_dir.iterdir())
    assert files == ["alpha.json", "beta.json"]


# ─── render_human_report ────────────────────────────────────────────────────


def test_human_report_includes_project_header() -> None:
    delta = _make_delta(project_name="demo")
    rsync = _make_rsync()
    text = render_human_report(delta, rsync)
    assert "=== project: demo ===" in text
    assert "raw:    raw/demo" in text
    assert "wiki:   wiki/demo" in text


def test_human_report_includes_rsync_stats() -> None:
    delta = _make_delta()
    rsync = _make_rsync(transferred=3, deleted=1)
    text = render_human_report(delta, rsync)
    assert "rsync:  exit=0" in text
    assert "3 .md files transferred, 1 deleted" in text


def test_human_report_marks_dry_run() -> None:
    delta = _make_delta()
    rsync = _make_rsync(transferred=0, dry_run=True)
    text = render_human_report(delta, rsync)
    assert "(dry-run)" in text


def test_human_report_with_no_rsync() -> None:
    delta = _make_delta()
    text = render_human_report(delta, None)
    assert "rsync:  (skipped)" in text


def test_human_report_lists_each_section() -> None:
    delta = _make_delta(new=2, modified=1, deleted=1)
    text = render_human_report(delta, None)
    assert "NEW (2):" in text
    assert "MODIFIED (1):" in text
    assert "DELETED (1):" in text


def test_human_report_truncates_past_limit() -> None:
    delta = _make_delta(new=DEFAULT_LIST_LIMIT + 5)
    text = render_human_report(delta, None)
    assert "... and 5 more" in text


def test_human_report_summary_only_mode() -> None:
    delta = _make_delta(new=10, modified=5)
    full = render_human_report(delta, None, summary_only=False)
    summary = render_human_report(delta, None, summary_only=True)
    # Per-file lines vanish in summary mode
    assert "new-0.md" in full
    assert "new-0.md" not in summary
    # But the counts stay
    assert "NEW (10):" in summary
    assert "MODIFIED (5):" in summary


def test_human_report_skipped_symlinks_section() -> None:
    delta = _make_delta(skipped=2)
    text = render_human_report(delta, None)
    assert "SYMLINKS skipped (2" in text
    assert "link-0.md" in text


def test_human_report_no_symlinks_section_if_empty() -> None:
    delta = _make_delta(new=1)  # no skipped symlinks
    text = render_human_report(delta, None)
    assert "SYMLINKS skipped" not in text


# ─── render_run_summary ────────────────────────────────────────────────────


def test_run_summary_aggregates() -> None:
    deltas = [
        _make_delta(project_name="a", new=2, modified=1),
        _make_delta(project_name="b", deleted=3),
    ]
    rsyncs: list = [
        _make_rsync(transferred=2, deleted=0),
        _make_rsync(transferred=0, deleted=3),
    ]
    text = render_run_summary(list(zip(deltas, rsyncs)))
    assert "projects synced: 2" in text
    assert "2 transferred, 3 deleted" in text
    assert "NEW=2" in text
    assert "MODIFIED=1" in text
    assert "DELETED=3" in text
    assert "total=6" in text


def test_run_summary_zero_changes_says_up_to_date() -> None:
    deltas = [_make_delta(project_name="a")]
    text = render_run_summary([(deltas[0], _make_rsync())])
    assert "wiki is up to date." in text


def test_run_summary_handles_none_rsync() -> None:
    deltas = [_make_delta(project_name="a", new=1)]
    text = render_run_summary([(deltas[0], None)])
    # Doesn't crash on None rsync; reports 0 transferred/deleted
    assert "0 transferred, 0 deleted" in text
    assert "NEW=1" in text
