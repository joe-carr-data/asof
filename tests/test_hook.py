"""Tests for templates/hooks/wiki_change_reminder.py.

The hook is a stand-alone script that lives outside the sync skill (it's
installed into a user project's .claude/hooks/ by asof:init). We import its
`main()` directly so tests don't have to spawn subprocesses for every case;
a single subprocess test exercises the script-as-a-script path end-to-end.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "templates" / "hooks" / "wiki_change_reminder.py"


@pytest.fixture(scope="module")
def hook_main():
    """Import the hook module by absolute path (it's in templates/, not on
    pyproject.toml's pythonpath)."""
    spec = importlib.util.spec_from_file_location("wiki_change_reminder", HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main


def _payload(
    *, tool: str = "Write", file_path: str = "/tmp/x.md"
) -> str:
    return json.dumps({"tool_name": tool, "tool_input": {"file_path": file_path}})


def _env(*, project_root: Path, project_name: str = "demo", wiki_dir: Path) -> dict[str, str]:
    return {
        "ASOF_PROJECT_ROOT": str(project_root),
        "ASOF_PROJECT_NAME": project_name,
        "ASOF_DIR": str(wiki_dir),
    }


# ─── env requirements ──────────────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["ASOF_PROJECT_ROOT", "ASOF_PROJECT_NAME", "ASOF_DIR"])
def test_silent_no_op_when_env_missing(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path, missing: str
) -> None:
    env = _env(project_root=tmp_path, wiki_dir=tmp_path)
    env.pop(missing)
    rc = hook_main(_payload(file_path=str(tmp_path / "x.md")), env)
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_silent_no_op_when_env_empty_string(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    env = _env(project_root=tmp_path, wiki_dir=tmp_path)
    env["ASOF_PROJECT_NAME"] = "   "  # whitespace-only counts as missing
    rc = hook_main(_payload(file_path=str(tmp_path / "x.md")), env)
    assert rc == 0
    assert capsys.readouterr().out == ""


# ─── trigger filtering ──────────────────────────────────────────────────────


def test_fires_on_md_write_inside_project(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    md = project / "docs" / "x.md"
    md.parent.mkdir(parents=True)
    md.write_text("# x")

    rc = hook_main(_payload(file_path=str(md)), _env(project_root=project, wiki_dir=wiki))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "[asof:wiki-reminder]" in msg
    assert "docs/x.md" in msg
    assert "demo" in msg
    assert "/asof:sync demo" in msg


@pytest.mark.parametrize("tool", ["Edit", "MultiEdit", "NotebookEdit"])
def test_fires_on_other_triggering_tools(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path, tool: str
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    md = project / "x.md"
    md.write_text("# x")
    rc = hook_main(
        _payload(tool=tool, file_path=str(md)),
        _env(project_root=project, wiki_dir=wiki),
    )
    assert rc == 0
    assert capsys.readouterr().out  # something emitted


def test_silent_on_non_triggering_tool(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    rc = hook_main(
        _payload(tool="Bash", file_path=str(project / "x.md")),
        _env(project_root=project, wiki_dir=tmp_path / "wiki"),
    )
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_silent_on_non_md_file(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    py = project / "foo.py"
    py.write_text("# py")
    rc = hook_main(
        _payload(file_path=str(py)),
        _env(project_root=project, wiki_dir=tmp_path / "wiki"),
    )
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_silent_on_md_outside_project(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    md = elsewhere / "x.md"
    md.write_text("# x")
    rc = hook_main(
        _payload(file_path=str(md)),
        _env(project_root=project, wiki_dir=tmp_path / "wiki"),
    )
    assert rc == 0
    assert capsys.readouterr().out == ""


# ─── path-traversal + Pattern C guard (gpt-5.2-pro round-2 phase-2 HIGH) ──


@pytest.mark.parametrize(
    "evil_name",
    [
        "../escape",
        "../../etc/passwd",
        "foo/../bar",
        "/absolute/path",
        "with spaces",  # slug regex rejects
        "UpperCase",  # slug regex requires lowercase
        "..",
        "trailing-",  # slug regex requires alnum end
        "-leading",  # slug regex requires alnum start
        "",
    ],
)
def test_path_traversal_in_project_name_silently_no_ops(
    hook_main,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    evil_name: str,
) -> None:
    """ASOF_PROJECT_NAME flows into a filesystem path. Untrusted env values
    that don't match the slug regex must NOT escape `.pending-sync/`.
    Hook silently no-ops rather than allowing the write.
    """
    project = tmp_path / "proj"
    project.mkdir()
    md = project / "x.md"
    md.write_text("# x")
    env = _env(project_root=project, project_name=evil_name, wiki_dir=tmp_path / "wiki")
    rc = hook_main(_payload(file_path=str(md)), env)
    assert rc == 0
    assert capsys.readouterr().out == ""
    # Verify no escape happened: the literal evil-named directory should NOT
    # appear anywhere outside the wiki dir.
    suspicious = list(tmp_path.glob("**/escape*")) + list(tmp_path.glob("**/passwd*"))
    assert suspicious == []


def test_pattern_c_feedback_loop_excluded(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """In Pattern C, wiki_dir is INSIDE project_root. Editing wiki pages
    must NOT trigger reminders (would cause infinite loop during ingest).
    """
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wiki = repo / ".asof"
    wiki.mkdir()
    # Wiki page being edited (inside project root AND inside wiki dir)
    wiki_page = wiki / "wiki" / "myrepo" / "current_state.md"
    wiki_page.parent.mkdir(parents=True)
    wiki_page.write_text("# state")

    rc = hook_main(
        _payload(file_path=str(wiki_page)),
        _env(project_root=repo, wiki_dir=wiki),
    )
    assert rc == 0
    # Must NOT emit — wiki edits don't trigger reminders
    assert capsys.readouterr().out == ""


def test_pattern_c_source_edits_still_fire(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The Pattern C exclusion must NOT prevent reminders for legitimate
    source-doc edits inside the repo (only wiki internals are excluded).
    """
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wiki = repo / ".asof"
    wiki.mkdir()
    # Source doc inside the repo but OUTSIDE the wiki dir
    source_md = repo / "docs" / "design.md"
    source_md.parent.mkdir()
    source_md.write_text("# design")

    rc = hook_main(
        _payload(file_path=str(source_md)),
        _env(project_root=repo, wiki_dir=wiki),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert out  # Must emit
    assert "[asof:wiki-reminder]" in out


def test_silent_on_malformed_input(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    rc = hook_main(
        "not valid json {",
        _env(project_root=tmp_path, wiki_dir=tmp_path / "wiki"),
    )
    assert rc == 0
    assert capsys.readouterr().out == ""


# ─── never-throw guarantee (gpt-5.2-pro round-1 phase-2 CRITICAL) ──────────


def test_returns_zero_on_unwritable_wiki_dir(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The hook must NEVER let a filesystem error escape as a non-zero exit.
    PostToolUse contract: hook errors surface to the user as broken tool
    calls, which is wrong semantics for a benign reminder.

    Simulate by pointing ASOF_DIR at an unwritable parent.
    """
    project = tmp_path / "proj"
    project.mkdir()
    md = project / "x.md"
    md.write_text("# x")

    # Make a parent dir read-only so .pending-sync/ can't be created.
    readonly = tmp_path / "readonly"
    readonly.mkdir(mode=0o555)
    wiki = readonly / "wiki"
    # mkdir() inside a 0o555 dir will fail — the hook must catch this.
    try:
        rc = hook_main(
            _payload(file_path=str(md)),
            _env(project_root=project, wiki_dir=wiki),
        )
        assert rc == 0
    finally:
        # Restore perms so pytest cleanup doesn't fail
        readonly.chmod(0o755)


def test_returns_zero_on_corrupted_payload_with_unexpected_shape(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Edge case: valid JSON but unexpected structure (no tool_input, etc.)."""
    project = tmp_path / "proj"
    project.mkdir()
    rc = hook_main(
        '{"unexpected": "shape"}',
        _env(project_root=project, wiki_dir=tmp_path / "wiki"),
    )
    assert rc == 0
    assert capsys.readouterr().out == ""


# ─── debounce (per-project) ────────────────────────────────────────────────


def test_debounce_suppresses_within_window(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Codex round-2 fix: per-project debounce file; second invocation
    within DEBOUNCE_SECONDS produces no output."""
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    md = project / "x.md"
    md.write_text("# x")
    env = _env(project_root=project, wiki_dir=wiki)
    payload = _payload(file_path=str(md))

    # First call → emits
    rc1 = hook_main(payload, env, now=1000.0)
    assert rc1 == 0
    out1 = capsys.readouterr().out
    assert out1  # has content

    # Second call within 30s → silent
    rc2 = hook_main(payload, env, now=1010.0)
    assert rc2 == 0
    assert capsys.readouterr().out == ""


def test_debounce_releases_after_window(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    md = project / "x.md"
    md.write_text("# x")
    env = _env(project_root=project, wiki_dir=wiki)
    payload = _payload(file_path=str(md))

    hook_main(payload, env, now=1000.0)
    capsys.readouterr()  # drain
    # Beyond the 30s window, the hook fires again
    rc = hook_main(payload, env, now=1031.0)
    assert rc == 0
    assert capsys.readouterr().out


def test_debounce_is_per_project(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A reminder for project A must NOT suppress one for project B."""
    proj_a = tmp_path / "a"
    proj_a.mkdir()
    proj_b = tmp_path / "b"
    proj_b.mkdir()
    wiki = tmp_path / "wiki"
    (proj_a / "x.md").write_text("# x")
    (proj_b / "x.md").write_text("# x")

    # Fire for project a
    hook_main(
        _payload(file_path=str(proj_a / "x.md")),
        _env(project_root=proj_a, project_name="a", wiki_dir=wiki),
        now=1000.0,
    )
    assert capsys.readouterr().out

    # Fire for project b within debounce window of a → must NOT be suppressed
    hook_main(
        _payload(file_path=str(proj_b / "x.md")),
        _env(project_root=proj_b, project_name="b", wiki_dir=wiki),
        now=1010.0,
    )
    out = capsys.readouterr().out
    assert out
    assert "/asof:sync b" in out


def test_debounce_creates_per_project_stamp(
    hook_main, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    md = project / "x.md"
    md.write_text("# x")
    hook_main(
        _payload(file_path=str(md)),
        _env(project_root=project, project_name="demo", wiki_dir=wiki),
    )
    stamp = wiki / ".pending-sync" / "demo.stamp"
    assert stamp.is_file()


# ─── atomic O_EXCL claim (gpt-5.2-pro round-1 phase-2 HIGH) ────────────────


def test_atomic_claim_fresh_stamp_suppresses(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """If another process already created a fresh stamp via O_EXCL, this
    invocation must NOT emit (it lost the race). Simulates parallel-fire."""
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    md = project / "x.md"
    md.write_text("# x")
    env = _env(project_root=project, wiki_dir=wiki)

    # Pre-create the stamp at "now" to simulate winner-already-claimed
    stamp_dir = wiki / ".pending-sync"
    stamp_dir.mkdir(parents=True)
    stamp = stamp_dir / "demo.stamp"
    stamp.touch()
    os.utime(stamp, (1000.0, 1000.0))

    rc = hook_main(_payload(file_path=str(md)), env, now=1010.0)  # within 30s
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_atomic_claim_stale_stamp_refreshes_and_emits(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """If the existing stamp is stale (>DEBOUNCE_SECONDS old), the hook
    refreshes it and emits."""
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    md = project / "x.md"
    md.write_text("# x")
    env = _env(project_root=project, wiki_dir=wiki)

    stamp_dir = wiki / ".pending-sync"
    stamp_dir.mkdir(parents=True)
    stamp = stamp_dir / "demo.stamp"
    stamp.touch()
    os.utime(stamp, (1000.0, 1000.0))  # very old

    rc = hook_main(_payload(file_path=str(md)), env, now=2000.0)  # >> 30s later
    assert rc == 0
    assert capsys.readouterr().out  # emitted
    # Stamp mtime refreshed to 2000.0
    assert stamp.stat().st_mtime == pytest.approx(2000.0, abs=1.0)


# ─── lock detection ─────────────────────────────────────────────────────────


def test_appends_sync_in_progress_when_lock_recent(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    # Simulate an active sync by touching the lock file with current mtime
    lock = wiki / ".asof.lock"
    lock.touch()
    md = project / "x.md"
    md.write_text("# x")

    hook_main(
        _payload(file_path=str(md)),
        _env(project_root=project, wiki_dir=wiki),
    )
    out = json.loads(capsys.readouterr().out)
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "sync in progress" in msg


def test_no_sync_in_progress_when_lock_stale(
    hook_main, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    lock = wiki / ".asof.lock"
    lock.touch()
    # Backdate the lock by 1 hour — too old to be considered "active"
    old = time.time() - 3600
    os.utime(lock, (old, old))
    md = project / "x.md"
    md.write_text("# x")

    hook_main(
        _payload(file_path=str(md)),
        _env(project_root=project, wiki_dir=wiki),
    )
    out = json.loads(capsys.readouterr().out)
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "sync in progress" not in msg


# ─── end-to-end as subprocess ──────────────────────────────────────────────


def test_script_runs_as_subprocess(tmp_path: Path) -> None:
    """The hook is invoked as a script by Claude Code. Verify the
    if __name__ == '__main__' path works end-to-end."""
    project = tmp_path / "proj"
    project.mkdir()
    wiki = tmp_path / "wiki"
    md = project / "x.md"
    md.write_text("# x")

    payload = _payload(file_path=str(md))
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "ASOF_PROJECT_ROOT": str(project),
            "ASOF_PROJECT_NAME": "demo",
            "ASOF_DIR": str(wiki),
        },
        check=False,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "[asof:wiki-reminder]" in out["hookSpecificOutput"]["additionalContext"]
