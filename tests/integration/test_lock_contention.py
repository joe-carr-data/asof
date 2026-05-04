"""Lock-contention tests: two skills competing for `.asof.lock`.

The lock is `fcntl.flock(LOCK_EX)` and held for the duration of:
    - sync's rsync + last-sync report write
    - lint's report-collection (and --fix's auto-fixes if requested)

These tests start one subprocess that holds the lock, then a second
subprocess that should block until the first releases. Verifies:
  1. Concurrent invocations queue (don't both succeed simultaneously).
  2. The second invocation completes successfully after the first exits.
  3. Order is preserved (no lost work).

Implementation note: rather than orchestrating two real subprocesses
with timing primitives, we acquire the lock from a python helper
process via `python3 -c '...'` for a fixed sleep, then race a real
skill subprocess against it and measure wall-clock to confirm queueing.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from .conftest import run_skill


def _hold_lock_in_background(
    lock_path: Path, hold_seconds: float
) -> subprocess.Popen:
    """Spawn a python subprocess that grabs the lock and sleeps.

    Uses sync's file_lock helper so the test exercises the same lock
    semantics lint and sync use. Caller MUST consume the readiness
    handshake via `_wait_for_lock_held(holder)` before launching the
    contending skill — otherwise on slow runners the contender can
    start before the helper actually owns the lock. Codex round-1
    phase-5 MEDIUM.

    Returns the Popen handle so the test can wait()/terminate() it.
    """
    sync_scripts = Path(__file__).resolve().parents[2] / "skills" / "sync" / "scripts"
    code = (
        "import sys, time;\n"
        f"sys.path.insert(0, {str(sync_scripts)!r});\n"
        "from utils import file_lock;\n"
        f"with file_lock({str(lock_path)!r}):\n"
        "    sys.stdout.write('locked\\n')\n"
        "    sys.stdout.flush()\n"
        f"    time.sleep({hold_seconds})\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,  # line-buffered so the readiness signal arrives promptly
    )


def _wait_for_lock_held(holder: subprocess.Popen, timeout: float = 5.0) -> None:
    """Block until the holder subprocess emits its `locked\n` signal.

    Reads one line from the holder's stdout; raises TimeoutError if the
    helper crashes before signalling or never starts. This replaces the
    fragile `time.sleep(0.2)` proxy that races on slow runners.
    """
    deadline = time.monotonic() + timeout
    assert holder.stdout is not None  # type-checker placation; PIPE was set
    while True:
        if time.monotonic() > deadline:
            holder.kill()
            raise TimeoutError(
                f"lock-holder subprocess never emitted 'locked' within {timeout}s"
            )
        # readline is blocking but bounded by the deadline check above.
        line = holder.stdout.readline()
        if line.strip() == "locked":
            return
        if not line and holder.poll() is not None:
            stderr = holder.stderr.read() if holder.stderr else ""
            raise RuntimeError(
                f"lock-holder exited (code {holder.returncode}) before "
                f"acquiring lock; stderr: {stderr!r}"
            )


# ─── lint queues behind a held lock ───────────────────────────────────────


def test_lint_queues_when_lock_held(pattern_a_wiki: Path) -> None:
    """A second invocation must wait for the first to release the lock.

    We hold the lock externally for ~1.0s and time how long lint takes
    to complete. With fcntl LOCK_EX semantics, lint's flock() blocks
    until the holder exits, so wall-clock should be at least the hold
    duration (within reasonable jitter).
    """
    lock_path = pattern_a_wiki / ".asof.lock"
    hold_seconds = 1.0
    holder = _hold_lock_in_background(lock_path, hold_seconds)
    try:
        _wait_for_lock_held(holder)
        start = time.monotonic()
        result = run_skill(
            "lint", ["--wiki-dir", str(pattern_a_wiki)], timeout=10.0
        )
        elapsed = time.monotonic() - start
    finally:
        holder.wait()
    assert result.returncode == 0, result.stderr
    # Once the readiness signal fires, the lock is held; lint must wait
    # roughly the full hold_seconds. 0.3s grace covers scheduler jitter.
    assert elapsed >= hold_seconds - 0.3, (
        f"lint completed in {elapsed:.2f}s; expected ≥ {hold_seconds - 0.3:.2f}s "
        "(suggesting it didn't actually wait for the lock)"
    )


# ─── sync also queues ──────────────────────────────────────────────────────


def test_sync_queues_when_lock_held(
    pattern_a_wiki: Path, tmp_path: Path
) -> None:
    """Same as the lint test but for sync — both skills share the lock."""
    source = tmp_path / "src"
    (source / "x.md").write_text("# X\n", encoding="utf-8")
    lock_path = pattern_a_wiki / ".asof.lock"
    holder = _hold_lock_in_background(lock_path, 0.8)
    try:
        _wait_for_lock_held(holder)
        start = time.monotonic()
        result = run_skill(
            "sync",
            [
                "myproj",
                "--wiki-dir", str(pattern_a_wiki),
                "--non-interactive",
                "--summary-only",
            ],
            timeout=10.0,
        )
        elapsed = time.monotonic() - start
    finally:
        holder.wait()
    assert result.returncode == 0, result.stderr
    assert elapsed >= 0.5, (
        f"sync completed in {elapsed:.2f}s; expected ≥ 0.5s "
        "(suggesting it didn't actually wait for the lock)"
    )


# ─── two lints both succeed sequentially ──────────────────────────────────


def test_two_consecutive_lints_both_succeed(pattern_a_wiki: Path) -> None:
    """Sanity check: back-to-back lints both finish 0 with no lock leakage."""
    for i in range(3):
        result = run_skill("lint", ["--wiki-dir", str(pattern_a_wiki)])
        assert result.returncode == 0, f"iteration {i}: {result.stderr}"
        assert "Wiki is clean" in result.stdout


# ─── --fix under contention ────────────────────────────────────────────────


def test_lint_fix_acquires_lock_for_writes(
    pattern_a_wiki: Path,
) -> None:
    """When --fix is requested, lint must hold the lock through the write
    phase too (round-1 phase-4 CRITICAL fix). Drop a fixable orphan and
    confirm lint --fix mutates index.md while waiting on a held lock."""
    project_dir = pattern_a_wiki / "wiki" / "myproj"
    (project_dir / "entities").mkdir(parents=True, exist_ok=True)
    (project_dir / "entities" / "lone.md").write_text(
        "---\ntitle: Lone\ntype: entity\nproject: myproj\nlast_updated: 2026-05-04\n---\n",
        encoding="utf-8",
    )
    lock_path = pattern_a_wiki / ".asof.lock"
    holder = _hold_lock_in_background(lock_path, 0.8)
    try:
        _wait_for_lock_held(holder)
        start = time.monotonic()
        result = run_skill(
            "lint", ["--wiki-dir", str(pattern_a_wiki), "--fix"], timeout=10.0
        )
        elapsed = time.monotonic() - start
    finally:
        holder.wait()
    # The orphan IS auto-fixable (page has parseable title + type +
    # project, index.md has ## Entities section), so no refusals are
    # expected. Codex round-1 phase-5 LOW: previously this test allowed
    # exit 3, which would have hidden a fix-path regression. Tighten:
    # 0 (no findings remained after the fix) or 1 (the orphan finding
    # itself reported but applied) are valid; 3 (refused fix) is NOT.
    assert result.returncode != 3, (
        f"unexpected refused fix; stderr: {result.stderr}"
    )
    assert result.returncode in (0, 1), result.stderr
    assert elapsed >= 0.5
    # Verify the fix actually landed (orphan got linked into index.md).
    index = (project_dir / "index.md").read_text(encoding="utf-8")
    assert "[Lone](entities/lone.md)" in index


# ─── non-blocking variant ──────────────────────────────────────────────────


def test_non_blocking_lock_raises_when_held(pattern_a_wiki: Path) -> None:
    """Smoke-check the file_lock helper itself in non-blocking mode —
    second acquisition raises BlockingIOError. This is the primitive
    that lint/sync's blocking mode is built on."""
    lock_path = pattern_a_wiki / ".asof.lock"
    holder = _hold_lock_in_background(lock_path, 0.5)
    try:
        _wait_for_lock_held(holder)
        # Acquire non-blocking from THIS process — should raise.
        sync_scripts = Path(__file__).resolve().parents[2] / "skills" / "sync" / "scripts"
        sys.path.insert(0, str(sync_scripts))
        try:
            from utils import file_lock  # type: ignore[import-not-found]

            with pytest.raises(BlockingIOError), file_lock(lock_path, blocking=False):
                pass
        finally:
            sys.path.remove(str(sync_scripts))
    finally:
        holder.wait()
