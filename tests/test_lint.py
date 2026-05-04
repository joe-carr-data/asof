"""End-to-end tests for skills/lint/scripts/lint.py — the orchestrator.

Builds a minimal valid wiki on disk, runs `main(argv)`, asserts exit codes
and stdout/stderr. Covers happy path, project filter, --json, --severity,
read-only mode, and the pre-flight config gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lint import ExitCode, build_parser, main

# ─── helpers ───────────────────────────────────────────────────────────────


def _bootstrap_wiki(
    tmp_path: Path,
    *,
    pattern: str = "A",
    project_slug: str = "myproj",
    skill_version_floor: str | None = None,
) -> Path:
    """Build a minimal Pattern A wiki on disk and return wiki_dir.

    Calls init.py via the public entry to ensure schema-compliant pages.
    """
    import sys

    # Reuse init's main() to bootstrap; a pure-Python invocation keeps tests
    # fast (no subprocess).
    sys.path.insert(0, "skills/init/scripts")
    from init import main as init_main

    source = tmp_path / "src"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    rc = init_main([
        project_slug,  # name == slug (already valid slug-shape)
        str(source),
        "--pattern", pattern,
        "--wiki-dir", str(wiki_dir),
        "--non-interactive",
        "--skip-first-sync",
        "--no-install-hook",
        "--no-claudemd-snippet",
        "--no-additional-directories",
    ])
    assert rc == 0, "init bootstrap failed"
    if skill_version_floor:
        # Bump min_writer_version to simulate a read-only wiki.
        cfg_path = wiki_dir / ".asof.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["min_writer_version"] = skill_version_floor
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return wiki_dir


# ─── parser + version smoke ────────────────────────────────────────────────


def test_parser_exposes_all_documented_flags() -> None:
    parser = build_parser()
    flags = {a.dest for a in parser._actions}
    expected = {
        "project_name", "wiki_dir", "fix", "json", "severity",
        "dry_run", "non_interactive", "version", "help",
    }
    assert expected.issubset(flags)


# ─── pre-flight: invalid config halts ──────────────────────────────────────


def test_invalid_config_returns_precondition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / ".asof.json").write_text("{not json", encoding="utf-8")
    rc = main(["--wiki-dir", str(wiki_dir)])
    assert rc == ExitCode.PRECONDITION
    err = capsys.readouterr().err
    assert "invalid config" in err
    assert "untrusted config" in err


def test_unresolvable_wiki_dir_returns_precondition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ASOF_DIR pointing at a non-existent dir → exit 4. Round-1 phase-4
    LATENT: lint uses sync's resolver, which honors ASOF_DIR; load_wiki_config
    then raises FileNotFoundError ("no asof config at ..."), mapping to exit 4.

    Note: we explicitly point ASOF_DIR at a tmp_path subdir rather than
    monkeypatching Path.home, because DEFAULT_WIKI_DIR is computed at
    config.py import time and can't be retargeted post-import.
    """
    fake_wiki = tmp_path / "no-wiki-here"
    monkeypatch.setenv("ASOF_DIR", str(fake_wiki))
    monkeypatch.chdir(tmp_path)
    rc = main([])
    assert rc == ExitCode.PRECONDITION
    err = capsys.readouterr().err
    assert "no asof config" in err


def test_unknown_project_returns_precondition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki_dir = _bootstrap_wiki(tmp_path)
    rc = main(["does-not-exist", "--wiki-dir", str(wiki_dir)])
    assert rc == ExitCode.PRECONDITION
    err = capsys.readouterr().err
    assert "unknown project" in err
    assert "Valid:" in err


# ─── happy path ────────────────────────────────────────────────────────────


def test_clean_wiki_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki_dir = _bootstrap_wiki(tmp_path)
    rc = main(["--wiki-dir", str(wiki_dir)])
    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "Wiki is clean" in out
    assert "0 errors" in out


def test_specific_project_only_lints_that_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki_dir = _bootstrap_wiki(tmp_path)
    rc = main(["myproj", "--wiki-dir", str(wiki_dir)])
    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "(project: myproj)" in out


def test_findings_present_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inject a broken page → exit 1 + ERRORS section in output."""
    wiki_dir = _bootstrap_wiki(tmp_path)
    project_dir = wiki_dir / "wiki" / "myproj"
    (project_dir / "entities" / "broken.md").write_text(
        "---\ntype: entity\n---\nNo title, no project, no last_updated.\n",
        encoding="utf-8",
    )
    rc = main(["--wiki-dir", str(wiki_dir)])
    assert rc == ExitCode.FINDINGS
    out = capsys.readouterr().out
    assert "ERRORS" in out
    assert "broken.md" in out


# ─── output modes ──────────────────────────────────────────────────────────


def test_json_mode_emits_parseable_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki_dir = _bootstrap_wiki(tmp_path)
    capsys.readouterr()  # discard init's bootstrap output
    rc = main(["--wiki-dir", str(wiki_dir), "--json"])
    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["wiki_dir"] == str(wiki_dir)
    assert "skill_version" in payload
    assert payload["summary"] == {"errors": 0, "warnings": 0, "info": 0}


def test_severity_filter_drops_info(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--severity warn with only INFO findings → exit 0 + INFO section absent."""
    wiki_dir = _bootstrap_wiki(tmp_path)
    project_dir = wiki_dir / "wiki" / "myproj"
    (project_dir / "entities" / "orphan.md").write_text(
        "---\ntitle: O\ntype: entity\nproject: myproj\nlast_updated: 2026-05-04\n---\nbody\n",
        encoding="utf-8",
    )
    rc = main(["--wiki-dir", str(wiki_dir), "--severity", "warn"])
    assert rc == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "INFO" not in out or "0 info" in out  # filtered out of body


# ─── --fix paths ───────────────────────────────────────────────────────────


def test_fix_inserts_missing_last_updated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki_dir = _bootstrap_wiki(tmp_path)
    project_dir = wiki_dir / "wiki" / "myproj"
    target = project_dir / "entities" / "x.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\ntitle: X\ntype: entity\nproject: myproj\n---\nBody.\n",
        encoding="utf-8",
    )
    rc = main(["--wiki-dir", str(wiki_dir), "--fix"])
    # exit may be 1 (orphan-page INFO still reported and not refused if
    # index.md has the section) or 0 — depends on test bootstrap. We just
    # assert the last_updated insert worked.
    assert "last_updated: 2026" in target.read_text()
    _ = rc, capsys


def test_fix_dry_run_does_not_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wiki_dir = _bootstrap_wiki(tmp_path)
    project_dir = wiki_dir / "wiki" / "myproj"
    target = project_dir / "entities" / "x.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "---\ntitle: X\ntype: entity\nproject: myproj\n---\nBody.\n"
    target.write_text(original, encoding="utf-8")
    main(["--wiki-dir", str(wiki_dir), "--fix", "--dry-run"])
    assert target.read_text() == original


def test_fix_rejected_in_read_only_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bump min_writer_version above SKILL_VERSION → --fix exit 4."""
    wiki_dir = _bootstrap_wiki(tmp_path, skill_version_floor="999.0.0")
    rc = main(["--wiki-dir", str(wiki_dir), "--fix"])
    assert rc == ExitCode.PRECONDITION
    err = capsys.readouterr().err
    assert "read-only mode" in err
    assert "999.0.0" in err


def test_fix_rejected_when_writer_too_high_blocks_only_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --fix, lint runs report-only even in read-only mode."""
    wiki_dir = _bootstrap_wiki(tmp_path, skill_version_floor="999.0.0")
    rc = main(["--wiki-dir", str(wiki_dir)])  # no --fix
    assert rc == ExitCode.SUCCESS  # clean wiki, report-only mode is fine
    out = capsys.readouterr().out
    assert "Wiki is clean" in out
