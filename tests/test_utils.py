"""Tests for skills/sync/scripts/utils.py.

Covers slugification, path-safety, atomic writes, file locking, version
discovery / comparison, and mtime helpers. All tests are pure-stdlib +
pytest with no external deps; run with:

    pytest tests/test_utils.py
"""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest
from utils import (
    SKILL_VERSION,
    _read_skill_version,
    atomic_write_json,
    compare_versions,
    ensure_inside,
    file_lock,
    get_mtime_iso,
    parse_version,
    slugify,
)

# ─── slugify ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("traddea", "traddea"),
        ("My Project", "my-project"),
        ("MIXED_Case-123", "mixed-case-123"),
        ("  spaces  ", "spaces"),
        ("a", "a"),
        ("a-b", "a-b"),
        ("project--with---dashes", "project-with-dashes"),
        ("UPPER", "upper"),
        ("café", "caf"),  # non-ascii stripped, then trailing hyphens removed
    ],
)
def test_slugify_valid(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "../etc/passwd",
        "foo/bar",
        "back\\slash",
        "with\x00nul",
    ],
)
def test_slugify_rejects_unsafe_input(bad: str) -> None:
    with pytest.raises(ValueError):
        slugify(bad)


@pytest.mark.parametrize("bad", [None, 123, ["foo"], {"a": 1}])
def test_slugify_rejects_non_string(bad: object) -> None:
    with pytest.raises(ValueError, match="must be a string"):
        slugify(bad)  # type: ignore[arg-type]


def test_slugify_rejects_too_long() -> None:
    long = "a" * 65
    with pytest.raises(ValueError, match="exceeds 64 chars"):
        slugify(long)


def test_slugify_64_char_boundary_ok() -> None:
    exactly_64 = "a" * 64
    assert slugify(exactly_64) == exactly_64


def test_slugify_normalizes_to_empty_after_filter() -> None:
    # Pure non-ASCII => stripped to empty after filter
    with pytest.raises(ValueError, match="empty slug"):
        slugify("中文")


# ─── ensure_inside ──────────────────────────────────────────────────────────


def test_ensure_inside_accepts_child(tmp_path: Path) -> None:
    parent = tmp_path
    child = tmp_path / "subdir" / "file.txt"
    child.parent.mkdir()
    child.write_text("hello")
    assert ensure_inside(child, parent) == child.resolve()


def test_ensure_inside_accepts_parent_itself(tmp_path: Path) -> None:
    assert ensure_inside(tmp_path, tmp_path) == tmp_path.resolve()


def test_ensure_inside_rejects_escape_via_dotdot(tmp_path: Path) -> None:
    parent = tmp_path / "wiki"
    parent.mkdir()
    bad = parent / ".." / "outside.txt"
    with pytest.raises(ValueError, match="outside"):
        ensure_inside(bad, parent)


def test_ensure_inside_rejects_absolute_outside(tmp_path: Path) -> None:
    parent = tmp_path / "wiki"
    parent.mkdir()
    with pytest.raises(ValueError, match="outside"):
        ensure_inside("/etc/passwd", parent)


def test_ensure_inside_resolves_symlinks(tmp_path: Path) -> None:
    """Symlink that points outside the parent must be rejected."""
    parent = tmp_path / "wiki"
    parent.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    # Create symlink inside parent that points outside
    link = parent / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        ensure_inside(link, parent)


# ─── atomic_write_json ──────────────────────────────────────────────────────


def test_atomic_write_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    atomic_write_json(target, {"a": 1, "b": [2, 3]})
    assert target.is_file()
    assert json.loads(target.read_text()) == {"a": 1, "b": [2, 3]}


def test_atomic_write_creates_parent_dirs(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "out.json"
    atomic_write_json(target, {"ok": True})
    assert target.is_file()


def test_atomic_write_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    target.write_text('{"old": true}')
    atomic_write_json(target, {"new": True})
    assert json.loads(target.read_text()) == {"new": True}


def test_atomic_write_leaves_no_temp_files_on_success(tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    atomic_write_json(target, {"a": 1})
    files = list(tmp_path.iterdir())
    assert files == [target]


def test_atomic_write_indented_and_sorted(tmp_path: Path) -> None:
    """Output is human-readable: indented + sorted keys + trailing newline."""
    target = tmp_path / "out.json"
    atomic_write_json(target, {"z": 1, "a": 2})
    content = target.read_text()
    assert content.endswith("\n")
    assert content.index('"a"') < content.index('"z"')  # sorted
    assert "\n  " in content  # indented


class _Unserializable:
    """Module-level so it has a stable repr / picklable identity."""


def test_atomic_write_cleans_up_temp_on_error(tmp_path: Path) -> None:
    """If json.dump raises, the temp file must be cleaned up."""
    target = tmp_path / "out.json"

    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": _Unserializable()})

    # No temp files left behind
    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], f"unexpected leftovers: {leftovers}"


# ─── file_lock ──────────────────────────────────────────────────────────────


def test_file_lock_creates_lockfile(tmp_path: Path) -> None:
    lock = tmp_path / ".asof.lock"
    with file_lock(lock):
        assert lock.is_file()


def test_file_lock_creates_parent_dirs(tmp_path: Path) -> None:
    lock = tmp_path / "deep" / ".asof.lock"
    with file_lock(lock):
        assert lock.is_file()


def test_file_lock_releases_after_exit(tmp_path: Path) -> None:
    """After the with-block exits, another process can acquire."""
    lock = tmp_path / ".asof.lock"
    with file_lock(lock):
        pass
    # Should be acquirable again immediately
    with file_lock(lock, blocking=False):
        pass


def _hold_lock_worker(lock_path: str, ready_evt, release_evt) -> None:
    """Worker for test_file_lock_non_blocking_fails_when_held.

    Module-level so multiprocessing 'spawn' mode can pickle it (local
    functions can't be pickled).
    """
    with file_lock(Path(lock_path)):
        ready_evt.set()
        release_evt.wait(timeout=10)


def test_file_lock_non_blocking_fails_when_held(tmp_path: Path) -> None:
    """When the lock is held by another process, blocking=False raises."""
    lock = tmp_path / ".asof.lock"
    ctx = multiprocessing.get_context("spawn")
    ready_evt = ctx.Event()
    release_evt = ctx.Event()
    proc = ctx.Process(
        target=_hold_lock_worker, args=(str(lock), ready_evt, release_evt)
    )
    proc.start()
    try:
        # Wait until the child confirms it has the lock
        assert ready_evt.wait(timeout=10), "child never reported holding the lock"
        # Now non-blocking acquire from this process must fail
        with pytest.raises(BlockingIOError), file_lock(lock, blocking=False):
            pass
    finally:
        release_evt.set()
        proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()
            proc.join()


# ─── get_mtime_iso ──────────────────────────────────────────────────────────


def test_get_mtime_iso_format(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text("hi")
    # Set mtime to a known value: 2026-04-26 12:00:00 local
    import datetime as dt

    target = dt.datetime(2026, 4, 26, 12, 0, 0).timestamp()
    os.utime(f, (target, target))
    assert get_mtime_iso(f) == "2026-04-26"


def test_get_mtime_iso_returns_string(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text("hi")
    result = get_mtime_iso(f)
    assert isinstance(result, str)
    # YYYY-MM-DD shape
    assert len(result) == 10
    assert result[4] == "-" and result[7] == "-"


# ─── parse_version / compare_versions ───────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.0.0", (1, 0, 0)),
        ("0.1.0-dev", (0, 1, 0)),
        ("1.2.3-rc1+sha.abc", (1, 2, 3)),
        ("10.20.30", (10, 20, 30)),
        ("1.0", (1, 0)),
        ("2", (2,)),
    ],
)
def test_parse_version_valid(raw: str, expected: tuple[int, ...]) -> None:
    assert parse_version(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "abc", "1.x.0", "1..0", None, 123])
def test_parse_version_invalid(bad: object) -> None:
    with pytest.raises(ValueError):
        parse_version(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("1.0.0", "1.0.0", 0),
        ("1.0.0", "1.0.1", -1),
        ("1.0.1", "1.0.0", 1),
        ("0.9.0", "1.0.0", -1),
        ("1.0", "1.0.0", 0),  # zero-padding
        ("2", "1.5.0", 1),
        ("1.0.0-dev", "1.0.0", 0),  # pre-release stripped
        ("1.0.0-rc1", "1.0.0-dev", 0),  # both stripped
    ],
)
def test_compare_versions(a: str, b: str, expected: int) -> None:
    assert compare_versions(a, b) == expected


# ─── SKILL_VERSION discovery ────────────────────────────────────────────────


def test_skill_version_from_real_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    """SKILL_VERSION should match the version in the actual plugin.json."""
    # The manifest lives at <repo>/.claude-plugin/plugin.json. Find it.
    repo_root = Path(__file__).resolve().parent.parent
    manifest = repo_root / ".claude-plugin" / "plugin.json"
    assert manifest.is_file(), "expected plugin.json at repo root"
    expected = json.loads(manifest.read_text())["version"]
    # Re-read with override unset so we test the manifest discovery path.
    monkeypatch.delenv("ASOF_SKILL_VERSION_OVERRIDE", raising=False)
    assert _read_skill_version() == expected


def test_skill_version_env_override(monkeypatch) -> None:
    monkeypatch.setenv("ASOF_SKILL_VERSION_OVERRIDE", "9.9.9-test")
    assert _read_skill_version() == "9.9.9-test"


def test_skill_version_constant_is_string() -> None:
    # Imported value should be a string (computed at import time)
    assert isinstance(SKILL_VERSION, str)
    assert SKILL_VERSION  # non-empty
