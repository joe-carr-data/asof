"""End-to-end integration tests for skills/init/scripts/init.py.

Drives the full 5-stage flow with mocked subprocess (so first-sync doesn't
actually invoke sync.py), an isolated tmp source + wiki dir, and a
slug-validated synthetic project. Verifies exit codes, files written,
and key content.

The constituent modules (preflight, wizard, scaffold, integrations) have
their own unit tests; these tests focus on the orchestration and CLI surface.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from init import ExitCode, build_parser, main

# ─── CLI ───────────────────────────────────────────────────────────────────


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "asof:init" in capsys.readouterr().out


def test_help_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "5-stage" in out or "Five-stage" in out


def test_parser_exposes_all_flags() -> None:
    parser = build_parser()
    actions = {a.dest for a in parser._actions}
    assert "project_name" in actions
    assert "source_path" in actions
    assert "pattern" in actions
    assert "wiki_dir" in actions
    assert "non_interactive" in actions
    assert "dry_run" in actions
    assert "no_install_hook" in actions
    assert "no_claudemd_snippet" in actions
    assert "no_additional_directories" in actions
    assert "skip_first_sync" in actions
    assert "commit_settings" in actions
    assert "import_existing" in actions


# ─── stub paths ────────────────────────────────────────────────────────────


def test_import_existing_returns_not_implemented(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """v1 stub: --import-existing returns exit code 5 with a clear message."""
    rc = main(["--import-existing", str(tmp_path)])
    assert rc == ExitCode.NOT_IMPLEMENTED
    err = capsys.readouterr().err
    assert "not yet implemented" in err
    assert "PLAN.md section 13" in err


def test_missing_required_args_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main([])
    assert rc == ExitCode.SCAFFOLD_ERROR
    err = capsys.readouterr().err
    assert "are required" in err


def test_invalid_project_name_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """slugify rejects names with path separators."""
    source = tmp_path / "src"
    source.mkdir()
    rc = main(["../escape", str(source), "--non-interactive"])
    assert rc == ExitCode.SCAFFOLD_ERROR
    err = capsys.readouterr().err
    assert "invalid project_name" in err


# ─── preflight failure path ────────────────────────────────────────────────


def test_preflight_failure_aborts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the preflight check finds a required failure, init aborts with exit 2."""
    monkeypatch.setattr("preflight.shutil.which", lambda _: None)
    source = tmp_path / "src"
    source.mkdir()
    rc = main(
        [
            "myproject",
            str(source),
            "--non-interactive",
            "--wiki-dir",
            str(tmp_path / "wiki"),
            "--skip-first-sync",
        ]
    )
    assert rc == ExitCode.PREFLIGHT_FAILED
    err = capsys.readouterr().err
    assert "required dependencies missing" in err


# ─── happy path: Pattern A end-to-end ──────────────────────────────────────


def _stub_subprocess(monkeypatch: pytest.MonkeyPatch, returncode: int = 0) -> list:
    """Replace subprocess.run inside integrations with a stub.

    Caveat: monkeypatch.setattr("integrations.subprocess.run", ...) actually
    patches the global subprocess.run (Python module aliasing), so this stub
    also intercepts preflight.check_rsync's rsync --version call. We
    distinguish by the first arg: if it's `sys.executable` we treat it as
    init's first-sync invocation and capture; otherwise we delegate to the
    real subprocess.run so preflight still works.
    """
    captured: list = []
    real_run = __import__("subprocess").run

    class FakeProc:
        """Subprocess-compatible result for the first-sync invocation."""

        def __init__(self) -> None:
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    def fake_run(args: list[str], **kwargs: Any):
        # init's first-sync call uses sys.executable as args[0].
        if args and args[0] == sys.executable:
            captured.append((args, kwargs))
            return FakeProc()
        # Anything else (preflight's rsync --version, etc.) → real call.
        return real_run(args, **kwargs)

    monkeypatch.setattr("integrations.subprocess.run", fake_run)
    return captured


def test_full_run_pattern_a(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    rc = main(
        [
            "Demo Project",
            str(source),
            "--pattern",
            "A",
            "--wiki-dir",
            str(wiki_dir),
            "--non-interactive",
        ]
    )
    assert rc == ExitCode.SUCCESS

    # Wiki dir + structure created
    assert wiki_dir.is_dir()
    assert (wiki_dir / "raw").is_dir()
    assert (wiki_dir / "wiki").is_dir()
    assert (wiki_dir / "CLAUDE.md").is_file()
    assert (wiki_dir / ".asof.json").is_file()
    # Project dir scaffolded
    project_dir = wiki_dir / "wiki" / "demo-project"
    assert project_dir.is_dir()
    for f in ("index.md", "log.md", "_candidates.md", "current_state.md"):
        assert (project_dir / f).is_file()
    # Subdirs created
    assert (project_dir / "entities").is_dir()
    assert (project_dir / "concepts").is_dir()
    assert (project_dir / "sources").is_dir()
    # Integrations: CLAUDE.md snippet + hook + settings
    assert (source / "CLAUDE.md").is_file()
    assert "asof-wiki:precedence-block" in (source / "CLAUDE.md").read_text()
    assert (source / ".claude" / "hooks" / "asof_wiki_change_reminder.py").is_file()
    settings = source / ".claude" / "settings.local.json"
    assert settings.is_file()
    cfg = json.loads(settings.read_text())
    assert str(wiki_dir) in cfg["permissions"]["additionalDirectories"]
    assert "PostToolUse" in cfg["hooks"]


def test_full_run_pattern_c(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch)
    source = tmp_path / "myrepo"
    source.mkdir()
    rc = main(
        [
            "MyRepo",
            str(source),
            "--pattern",
            "C",
            "--non-interactive",
        ]
    )
    assert rc == ExitCode.SUCCESS

    # Pattern C: wiki dir = <source>/.asof
    wiki_dir = source / ".asof"
    assert wiki_dir.is_dir()
    cfg = json.loads((wiki_dir / ".asof.json").read_text())
    assert "wiki_dir" not in cfg
    proj = cfg["projects"][0]
    assert "source" not in proj  # auto-derived
    # additionalDirectories must NOT include wiki_dir for Pattern C
    settings = source / ".claude" / "settings.local.json"
    if settings.is_file():
        scfg = json.loads(settings.read_text())
        perms = scfg.get("permissions", {})
        addl = perms.get("additionalDirectories", [])
        assert str(wiki_dir) not in addl


def test_dry_run_creates_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    rc = main(
        [
            "Demo",
            str(source),
            "--pattern",
            "A",
            "--wiki-dir",
            str(wiki_dir),
            "--non-interactive",
            "--dry-run",
        ]
    )
    assert rc == ExitCode.SUCCESS
    # Nothing written
    assert not wiki_dir.exists()
    assert not (source / "CLAUDE.md").exists()
    assert not (source / ".claude").exists()
    out = capsys.readouterr().out
    assert "(dry-run mode" in out


def test_skip_first_sync_no_subprocess(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    main(
        [
            "Demo",
            str(source),
            "--pattern",
            "A",
            "--wiki-dir",
            str(tmp_path / "wiki"),
            "--non-interactive",
            "--skip-first-sync",
        ]
    )
    # subprocess.run was never called
    assert captured == []


def test_no_install_hook_flag(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    main(
        [
            "Demo",
            str(source),
            "--pattern",
            "A",
            "--wiki-dir",
            str(tmp_path / "wiki"),
            "--non-interactive",
            "--no-install-hook",
            "--skip-first-sync",
        ]
    )
    # Hook script should NOT exist
    hook = source / ".claude" / "hooks" / "asof_wiki_change_reminder.py"
    assert not hook.exists()
    # Settings file should still exist (additionalDirectories was kept)
    settings = source / ".claude" / "settings.local.json"
    assert settings.is_file()
    cfg = json.loads(settings.read_text())
    # No hooks block
    assert "hooks" not in cfg


def test_no_claudemd_snippet_flag(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    main(
        [
            "Demo",
            str(source),
            "--pattern",
            "A",
            "--wiki-dir",
            str(tmp_path / "wiki"),
            "--non-interactive",
            "--no-claudemd-snippet",
            "--skip-first-sync",
        ]
    )
    assert not (source / "CLAUDE.md").exists()


def test_commit_settings_flag(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    main(
        [
            "Demo",
            str(source),
            "--pattern",
            "A",
            "--wiki-dir",
            str(tmp_path / "wiki"),
            "--non-interactive",
            "--commit-settings",
            "--skip-first-sync",
        ]
    )
    # Should write to settings.json (committed), not settings.local.json
    assert (source / ".claude" / "settings.json").is_file()
    assert not (source / ".claude" / "settings.local.json").exists()


def test_default_writes_to_settings_local(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    main(
        [
            "Demo",
            str(source),
            "--pattern",
            "A",
            "--wiki-dir",
            str(tmp_path / "wiki"),
            "--non-interactive",
            "--skip-first-sync",
        ]
    )
    # Default behavior: settings.local.json (gitignored)
    assert (source / ".claude" / "settings.local.json").is_file()
    assert not (source / ".claude" / "settings.json").exists()


def test_re_running_with_same_slug_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second init with the same project slug must fail with exit 3
    (scaffold error)."""
    _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    args = [
        "Demo",
        str(source),
        "--pattern",
        "A",
        "--wiki-dir",
        str(tmp_path / "wiki"),
        "--non-interactive",
        "--skip-first-sync",
    ]
    rc1 = main(args)
    assert rc1 == ExitCode.SUCCESS
    rc2 = main(args)
    assert rc2 == ExitCode.SCAFFOLD_ERROR
    err = capsys.readouterr().err
    assert "already exists" in err


def test_post_init_wiki_is_lint_clean_against_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-trip: an init'd wiki must be loadable by sync's load_wiki_config
    (which validates against SCHEMA.md). This catches any divergence
    between init's output and the schema contract."""
    _stub_subprocess(monkeypatch)
    source = tmp_path / "src"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    main(
        [
            "Demo",
            str(source),
            "--pattern",
            "A",
            "--wiki-dir",
            str(wiki_dir),
            "--non-interactive",
            "--skip-first-sync",
        ]
    )
    # Use sync's bridge-loaded helper to validate the new config
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "sync" / "scripts"))
    from config import load_wiki_config

    cfg = load_wiki_config(wiki_dir)
    assert cfg.projects[0].name == "demo"
    assert cfg.is_pattern_c is False
    assert cfg.schema_version == "1.0"
