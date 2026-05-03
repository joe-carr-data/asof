"""Tests for skills/init/scripts/integrations.py — stage 5 actions.

Covers all four integrations (CLAUDE.md snippet, hook install, settings
update, first sync) plus the orchestrator. Subprocess.run is mocked for
the first-sync tests so we don't actually invoke sync.py from the test
suite (the sync skill has its own integration tests).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from integrations import (
    HOOK_MATCHER,
    HOOK_SCRIPT_FILENAME,
    SNIPPET_CLOSE_MARKER,
    SNIPPET_OPEN_MARKER,
    IntegrationRequest,
    IntegrationResult,
    _settings_path,
    append_claudemd_snippet,
    apply_integrations,
    install_hook,
    run_first_sync,
    update_settings,
)
from wizard import IntegrationChoice, LayoutChoice

TODAY = "2026-05-04"

# ─── fixtures / builders ───────────────────────────────────────────────────


def _build_request(
    tmp_path: Path,
    *,
    pattern: str = "A",
    install_claudemd_snippet: bool = True,
    install_hook_choice: bool = True,
    add_additional_directories: bool = True,
    run_first_sync_choice: bool = True,
    commit_settings: bool = False,
) -> IntegrationRequest:
    project_root = tmp_path / "project"
    project_root.mkdir()
    if pattern == "C":
        layout = LayoutChoice(
            pattern="C", wiki_dir=project_root / ".asof", source=None
        )
    else:
        wiki_dir = tmp_path / "wiki"
        layout = LayoutChoice(
            pattern=pattern,  # type: ignore[arg-type]
            wiki_dir=wiki_dir,
            source=project_root,
        )
    return IntegrationRequest(
        layout=layout,
        project_slug="myproject",
        project_display_name="My Project",
        project_root=project_root,
        choices=IntegrationChoice(
            install_claudemd_snippet=install_claudemd_snippet,
            install_hook=install_hook_choice,
            add_additional_directories=add_additional_directories,
            run_first_sync=run_first_sync_choice,
            commit_settings=commit_settings,
        ),
    )


# ─── 1) CLAUDE.md snippet ──────────────────────────────────────────────────


def test_snippet_creates_claudemd_when_missing(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    appended, skipped = append_claudemd_snippet(
        request, today=TODAY, dry_run=False
    )
    assert appended is True
    assert skipped is False
    target = request.project_root / "CLAUDE.md"
    assert target.is_file()
    text = target.read_text()
    assert SNIPPET_OPEN_MARKER in text
    assert SNIPPET_CLOSE_MARKER in text
    assert "myproject" in text
    assert str(request.layout.wiki_dir) in text


def test_snippet_appends_to_existing_claudemd(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    target = request.project_root / "CLAUDE.md"
    target.write_text("# Existing project doc\n\nSome content here.")
    append_claudemd_snippet(request, today=TODAY, dry_run=False)
    text = target.read_text()
    # Existing content preserved
    assert "Some content here." in text
    # Snippet appended after a blank-line separator
    assert "Some content here." in text
    assert SNIPPET_OPEN_MARKER in text
    # Snippet comes after the existing content
    assert text.index("Some content here.") < text.index(SNIPPET_OPEN_MARKER)


def test_snippet_skipped_when_open_marker_already_present(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    target = request.project_root / "CLAUDE.md"
    target.write_text(f"# Existing\n\n{SNIPPET_OPEN_MARKER}\nold content\n")
    appended, skipped = append_claudemd_snippet(
        request, today=TODAY, dry_run=False
    )
    assert appended is False
    assert skipped is True
    # File unchanged
    text = target.read_text()
    assert text.count(SNIPPET_OPEN_MARKER) == 1
    assert "old content" in text


def test_snippet_dry_run_no_write(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    appended, skipped = append_claudemd_snippet(
        request, today=TODAY, dry_run=True
    )
    assert appended is True  # would have appended
    target = request.project_root / "CLAUDE.md"
    assert not target.exists()


# ─── 2) Hook install ──────────────────────────────────────────────────────


def test_hook_copies_script(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    installed, skipped = install_hook(request, dry_run=False)
    assert installed is True
    assert skipped is False
    target = request.project_root / ".claude" / "hooks" / HOOK_SCRIPT_FILENAME
    assert target.is_file()
    # Verify the copied content matches the source template
    text = target.read_text()
    assert "ASOF_PROJECT_NAME" in text
    assert "_claim_debounce_slot" in text
    # Verify executable bit
    assert (target.stat().st_mode & 0o100) != 0


def test_hook_skipped_when_already_present(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    install_hook(request, dry_run=False)
    installed, skipped = install_hook(request, dry_run=False)
    assert installed is False
    assert skipped is True


def test_hook_dry_run_no_copy(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    installed, skipped = install_hook(request, dry_run=True)
    assert installed is True
    target = request.project_root / ".claude" / "hooks" / HOOK_SCRIPT_FILENAME
    assert not target.exists()


# ─── 3) Settings file ─────────────────────────────────────────────────────


def test_settings_path_default_is_local_json(tmp_path: Path) -> None:
    """Default behavior writes to settings.local.json (gitignored)."""
    request = _build_request(tmp_path, commit_settings=False)
    assert _settings_path(request).name == "settings.local.json"


def test_settings_path_commit_uses_settings_json(tmp_path: Path) -> None:
    request = _build_request(tmp_path, commit_settings=True)
    assert _settings_path(request).name == "settings.json"


def test_settings_creates_fresh_local_json(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    path, added, already = update_settings(request, dry_run=False)
    assert path.name == "settings.local.json"
    assert added is True
    assert already is False
    assert path.is_file()
    cfg = json.loads(path.read_text())
    # Pattern A includes additionalDirectories
    assert str(request.layout.wiki_dir) in cfg["permissions"]["additionalDirectories"]
    # PostToolUse hook entry present
    pt = cfg["hooks"]["PostToolUse"]
    assert len(pt) == 1
    assert pt[0]["matcher"] == HOOK_MATCHER
    inner = pt[0]["hooks"][0]
    assert inner["type"] == "command"
    assert inner["command"].endswith(HOOK_SCRIPT_FILENAME)
    assert inner["env"]["ASOF_PROJECT_NAME"] == "myproject"
    assert inner["env"]["ASOF_PROJECT_ROOT"] == str(request.project_root)
    assert inner["env"]["ASOF_DIR"] == str(request.layout.wiki_dir)


def test_settings_pattern_c_omits_additional_directories(tmp_path: Path) -> None:
    request = _build_request(
        tmp_path, pattern="C", add_additional_directories=False
    )
    path, added, already = update_settings(request, dry_run=False)
    assert added is False
    cfg = json.loads(path.read_text())
    # Permissions block may exist (empty) or be missing — either is fine
    perms = cfg.get("permissions", {})
    assert "additionalDirectories" not in perms or perms["additionalDirectories"] == []


def test_settings_preserves_existing_keys(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    settings_dir = request.project_root / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.local.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": ["Bash(ls *)"],
                    "additionalDirectories": ["/some/other/dir"],
                },
                "env": {"FOO": "bar"},
            }
        )
    )
    update_settings(request, dry_run=False)
    cfg = json.loads(
        (settings_dir / "settings.local.json").read_text()
    )
    # Existing keys preserved
    assert cfg["permissions"]["allow"] == ["Bash(ls *)"]
    assert cfg["env"]["FOO"] == "bar"
    # Wiki dir appended (not overwritten)
    assert "/some/other/dir" in cfg["permissions"]["additionalDirectories"]
    assert str(request.layout.wiki_dir) in cfg["permissions"]["additionalDirectories"]


def test_settings_idempotent_for_additional_directories(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    update_settings(request, dry_run=False)
    _, added2, already2 = update_settings(request, dry_run=False)
    assert added2 is False
    assert already2 is True


def test_settings_idempotent_for_hook_entry(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    update_settings(request, dry_run=False)
    update_settings(request, dry_run=False)
    cfg = json.loads(
        (request.project_root / ".claude" / "settings.local.json").read_text()
    )
    # Only one PostToolUse entry — no duplicate
    assert len(cfg["hooks"]["PostToolUse"]) == 1


def test_settings_commit_writes_to_committed_file(tmp_path: Path) -> None:
    request = _build_request(tmp_path, commit_settings=True)
    path, _, _ = update_settings(request, dry_run=False)
    assert path.name == "settings.json"
    assert path.is_file()
    # settings.local.json should NOT be touched
    assert not (path.parent / "settings.local.json").exists()


def test_settings_malformed_existing_raises(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    settings_dir = request.project_root / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.local.json").write_text("not valid {")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        update_settings(request, dry_run=False)


def test_settings_non_object_top_level_raises(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    settings_dir = request.project_root / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.local.json").write_text("[]")
    with pytest.raises(RuntimeError, match="must be a JSON object"):
        update_settings(request, dry_run=False)


def test_settings_dry_run_no_write(tmp_path: Path) -> None:
    request = _build_request(tmp_path)
    update_settings(request, dry_run=True)
    settings_dir = request.project_root / ".claude"
    assert not (settings_dir / "settings.local.json").exists()


def test_settings_only_additional_dirs_when_hook_disabled(tmp_path: Path) -> None:
    """If user opts out of hook but keeps additionalDirectories, settings
    file must NOT have a hooks block."""
    request = _build_request(tmp_path, install_hook_choice=False)
    path, _, _ = update_settings(request, dry_run=False)
    cfg = json.loads(path.read_text())
    assert "hooks" not in cfg
    assert str(request.layout.wiki_dir) in cfg["permissions"]["additionalDirectories"]


# ─── 4) First sync ────────────────────────────────────────────────────────


def test_first_sync_invokes_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _build_request(tmp_path)
    captured: dict[str, Any] = {}

    class FakeProc:
        returncode = 0

    def fake_run(args: list[str], **kwargs: Any) -> FakeProc:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("integrations.subprocess.run", fake_run)
    ran, exit_code = run_first_sync(request, dry_run=False)
    assert ran is True
    assert exit_code == 0
    assert "--project" in captured["args"]
    assert "myproject" in captured["args"]
    assert "--non-interactive" in captured["args"]
    assert "--wiki-dir" in captured["args"]


def test_first_sync_dry_run_no_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        "integrations.subprocess.run",
        lambda *_a, **_k: called.append(True),  # type: ignore[arg-type]
    )
    request = _build_request(tmp_path)
    ran, exit_code = run_first_sync(request, dry_run=True)
    assert ran is False
    assert exit_code is None
    assert called == []


def test_first_sync_propagates_non_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeProc:
        returncode = 1

    monkeypatch.setattr(
        "integrations.subprocess.run", lambda *_a, **_k: FakeProc()
    )
    request = _build_request(tmp_path)
    ran, exit_code = run_first_sync(request, dry_run=False)
    assert ran is True
    assert exit_code == 1  # non-zero surfaced; doesn't raise


# ─── orchestrator: apply_integrations ─────────────────────────────────────


def test_apply_integrations_runs_all_when_all_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All four integrations chosen → all four ran."""
    monkeypatch.setattr(
        "integrations.subprocess.run",
        lambda *_a, **_k: type("P", (), {"returncode": 0})(),
    )
    request = _build_request(tmp_path)
    result = apply_integrations(request, today=TODAY, dry_run=False)
    assert isinstance(result, IntegrationResult)
    assert result.snippet_appended is True
    assert result.hook_installed is True
    assert result.settings_path is not None
    assert result.additional_dir_added is True
    assert result.first_sync_ran is True
    assert result.first_sync_exit_code == 0


def test_apply_integrations_pattern_c_no_additional_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pattern C with add_additional_directories=False (forced by wizard.py
    for Pattern C). Other integrations still run."""
    monkeypatch.setattr(
        "integrations.subprocess.run",
        lambda *_a, **_k: type("P", (), {"returncode": 0})(),
    )
    request = _build_request(
        tmp_path, pattern="C", add_additional_directories=False
    )
    result = apply_integrations(request, today=TODAY, dry_run=False)
    assert result.additional_dir_added is False
    # But other things still happened
    assert result.snippet_appended is True
    assert result.hook_installed is True


def test_apply_integrations_skip_first_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        "integrations.subprocess.run",
        lambda *_a, **_k: called.append(True),  # type: ignore[arg-type]
    )
    request = _build_request(tmp_path, run_first_sync_choice=False)
    result = apply_integrations(request, today=TODAY, dry_run=False)
    assert result.first_sync_ran is False
    assert result.first_sync_exit_code is None
    assert called == []


def test_apply_integrations_no_hook_no_snippet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User opts out of both hook and snippet, keeps additional_dirs and sync."""
    monkeypatch.setattr(
        "integrations.subprocess.run",
        lambda *_a, **_k: type("P", (), {"returncode": 0})(),
    )
    request = _build_request(
        tmp_path,
        install_claudemd_snippet=False,
        install_hook_choice=False,
    )
    result = apply_integrations(request, today=TODAY, dry_run=False)
    assert result.snippet_appended is False
    assert result.hook_installed is False
    # Settings still updated for additionalDirectories alone
    assert result.settings_path is not None
    assert result.additional_dir_added is True
    # CLAUDE.md never created
    assert not (request.project_root / "CLAUDE.md").exists()
    # Hook script not copied
    assert not (
        request.project_root / ".claude" / "hooks" / HOOK_SCRIPT_FILENAME
    ).exists()


def test_apply_integrations_dry_run_no_filesystem_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[bool] = []
    monkeypatch.setattr(
        "integrations.subprocess.run",
        lambda *_a, **_k: called.append(True),  # type: ignore[arg-type]
    )
    request = _build_request(tmp_path)
    apply_integrations(request, today=TODAY, dry_run=True)
    # Nothing actually written
    assert not (request.project_root / "CLAUDE.md").exists()
    assert not (request.project_root / ".claude").exists()
    assert called == []


# ─── partial-failure handling (Codex round-1 phase-3 HIGH 2) ──────────────


def test_apply_integrations_no_errors_on_clean_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean run produces an empty errors tuple and has_errors=False."""
    monkeypatch.setattr(
        "integrations.subprocess.run",
        lambda *_a, **_k: type("P", (), {"returncode": 0})(),
    )
    request = _build_request(tmp_path)
    result = apply_integrations(request, today=TODAY, dry_run=False)
    assert result.errors == ()
    assert result.has_errors is False


def test_apply_integrations_settings_failure_does_not_abort_first_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed settings.json raises RuntimeError in update_settings, but
    apply_integrations still runs the first sync afterwards. The failure is
    captured in errors[]; other steps still report success."""
    sync_called: list[bool] = []

    def fake_run(*_a: Any, **_k: Any) -> Any:
        sync_called.append(True)
        return type("P", (), {"returncode": 0})()

    monkeypatch.setattr("integrations.subprocess.run", fake_run)
    request = _build_request(tmp_path)
    # Pre-create a malformed settings.local.json that update_settings rejects.
    settings_dir = request.project_root / ".claude"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.local.json").write_text("{not json", encoding="utf-8")

    result = apply_integrations(request, today=TODAY, dry_run=False)

    assert result.has_errors is True
    assert len(result.errors) == 1
    step_name, msg = result.errors[0]
    assert step_name == "settings update"
    assert "RuntimeError" in msg or "not valid JSON" in msg
    # Other steps before settings still succeeded
    assert result.snippet_appended is True
    assert result.hook_installed is True
    # First sync still ran (didn't get aborted by the settings failure)
    assert sync_called == [True]
    assert result.first_sync_ran is True


def test_apply_integrations_snippet_failure_does_not_abort_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When snippet-append raises OSError, hook install + settings still run.
    The snippet failure is captured in errors[]."""
    monkeypatch.setattr(
        "integrations.subprocess.run",
        lambda *_a, **_k: type("P", (), {"returncode": 0})(),
    )

    def boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("simulated CLAUDE.md write failure")

    monkeypatch.setattr("integrations.append_claudemd_snippet", boom)

    request = _build_request(tmp_path)
    result = apply_integrations(request, today=TODAY, dry_run=False)

    assert result.has_errors is True
    assert any(step == "CLAUDE.md snippet" for step, _ in result.errors)
    # Snippet did not record success
    assert result.snippet_appended is False
    # Hook + settings + first sync still ran
    assert result.hook_installed is True
    assert result.settings_path is not None
    assert result.first_sync_ran is True
