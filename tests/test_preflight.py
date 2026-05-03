"""Tests for skills/init/scripts/preflight.py — stage 1 system checks."""

from __future__ import annotations

import collections
import sys

import pytest
from preflight import (
    MIN_PYTHON,
    SEVERITY_INFORMATIONAL,
    SEVERITY_OPTIONAL,
    SEVERITY_RECOMMENDED,
    SEVERITY_REQUIRED,
    CheckResult,
    PreflightReport,
    check_git,
    check_obsidian,
    check_python,
    check_rsync,
    informational_no_extra_deps,
    render_preflight,
    run_preflight,
)

# ─── individual check helpers ──────────────────────────────────────────────


def test_check_python_passes_on_supported_version() -> None:
    """The test runner is itself running on Python ≥ 3.9 (per pyproject.toml's
    requires-python), so check_python must report present=True."""
    result = check_python()
    assert isinstance(result, CheckResult)
    assert result.severity == SEVERITY_REQUIRED
    assert result.present is True
    assert result.detected_version is not None
    # Version string format: "X.Y.Z"
    assert result.detected_version.count(".") == 2


def test_check_python_install_hint_mentions_min_version() -> None:
    result = check_python()
    assert f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+" in result.install_hint


def _fake_version(major: int, minor: int, micro: int) -> tuple:
    """Build a sys.version_info-compatible namedtuple.

    Using a real namedtuple (not a custom class) keeps tuple-comparison
    semantics intact — pytest's internals do `sys.version_info >= (3, 12, 0)`,
    which would TypeError against a custom class without `__ge__`.
    """
    VersionInfo = collections.namedtuple(
        "VersionInfo", ["major", "minor", "micro", "releaselevel", "serial"]
    )
    return VersionInfo(major, minor, micro, "final", 0)


def test_check_python_simulated_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force version_info into a failing range and verify the check
    reports `present=False` even though the runtime is fine."""
    monkeypatch.setattr(sys, "version_info", _fake_version(3, 7, 0))
    result = check_python()
    assert result.present is False
    assert result.detected_version == "3.7.0"


def test_check_rsync_present_on_dev_machines() -> None:
    """rsync ships with macOS by default; CI Linux installs it. Verify
    the check at least returns the required severity + a result. We don't
    insist on present=True because someone might run tests in a stripped
    container."""
    result = check_rsync()
    assert result.severity == SEVERITY_REQUIRED
    assert result.name == "rsync"


def test_check_rsync_simulated_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("preflight.shutil.which", lambda _: None)
    result = check_rsync()
    assert result.present is False
    assert result.detected_version is None
    assert "brew install rsync" in result.install_hint


def test_check_git_recommended_severity() -> None:
    result = check_git()
    assert result.severity == SEVERITY_RECOMMENDED


def test_check_git_simulated_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("preflight.shutil.which", lambda _: None)
    result = check_git()
    assert result.present is False
    assert "isn't required" in result.install_hint


def test_check_obsidian_optional_severity() -> None:
    result = check_obsidian()
    assert result.severity == SEVERITY_OPTIONAL
    # Optional means the result is never a blocker regardless of present
    assert result.detected_version is None  # we don't try to get version


def test_check_obsidian_install_hint_explains_value() -> None:
    result = check_obsidian()
    assert "graph view" in result.install_hint
    assert "obsidian.md" in result.install_hint


def test_informational_no_extra_deps_always_present() -> None:
    """Informational note must always render as present=True so it appears
    in the table without a fail glyph."""
    result = informational_no_extra_deps()
    assert result.present is True
    assert result.severity == SEVERITY_INFORMATIONAL
    # Verify the explicit "no pip / npm / uv" promise is in the hint
    for token in ("pip install", "npm install", "uv sync"):
        assert token in result.install_hint


# ─── orchestration ─────────────────────────────────────────────────────────


def test_run_preflight_includes_all_five_checks() -> None:
    report = run_preflight()
    assert isinstance(report, PreflightReport)
    assert len(report.checks) == 5
    names = {c.name for c in report.checks}
    assert "rsync" in names
    assert "git" in names
    assert "Obsidian" in names
    # Python check uses the dynamic MIN_PYTHON name
    py_name = f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+"
    assert py_name in names
    assert "Python deps / Node / uv" in names


def test_run_preflight_has_no_required_failure_on_dev_machine() -> None:
    """Sanity check: this test running implies Python is fine, and any dev
    machine has rsync. If this fails, something's wrong with the setup —
    not with the test."""
    report = run_preflight()
    assert report.has_required_failure is False
    assert report.required_failures == ()


def test_run_preflight_required_failure_when_python_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "version_info", _fake_version(2, 7, 18))
    report = run_preflight()
    assert report.has_required_failure is True
    failures = report.required_failures
    assert len(failures) >= 1
    py = next(c for c in failures if c.name.startswith("Python"))
    assert py.present is False


def test_run_preflight_required_failure_when_rsync_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("preflight.shutil.which", lambda _: None)
    report = run_preflight()
    # With shutil.which patched, rsync AND git both report missing —
    # but only rsync is REQUIRED, so only rsync counts as a required failure.
    assert report.has_required_failure is True
    rsync = next(c for c in report.required_failures if c.name == "rsync")
    assert rsync.present is False


# ─── rendering ─────────────────────────────────────────────────────────────


def test_render_includes_header_and_all_checks() -> None:
    report = run_preflight()
    text = render_preflight(report)
    assert "asof:init — preflight check" in text
    for c in report.checks:
        assert c.name in text


def test_render_uses_correct_glyphs(monkeypatch: pytest.MonkeyPatch) -> None:
    """✓ for present, ✗ for required-missing, ! for recommended-missing,
    · for optional-missing, ℹ for informational.

    We mock both `shutil.which` and `Path.exists` so the test is
    environment-independent — actual `/Applications/Obsidian.app` etc.
    on the developer machine doesn't affect the result.
    """
    monkeypatch.setattr("preflight.shutil.which", lambda _: None)
    monkeypatch.setattr(sys, "version_info", _fake_version(2, 7, 18))
    # Path.exists() is used by check_obsidian for /Applications/Obsidian.app.
    # Mock it to always return False so Obsidian reports missing.
    monkeypatch.setattr("preflight.Path.exists", lambda _: False)

    report = run_preflight()
    text = render_preflight(report)
    # Python required-fail and rsync required-fail → ✗ appears
    assert "✗" in text
    # git recommended-fail → ! appears
    assert "!" in text
    # Obsidian optional-fail → · appears
    assert "·" in text
    # Informational note always shown with ℹ
    assert "ℹ" in text


def test_render_includes_install_hints_for_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("preflight.shutil.which", lambda _: None)
    report = run_preflight()
    text = render_preflight(report)
    # Failed checks' install hints must surface in the Notes section
    assert "brew install rsync" in text


def test_render_includes_informational_note_even_when_all_pass() -> None:
    """The 'no pip / uv / npm needed' note is informational — it must
    render every time, not just when something's missing."""
    report = run_preflight()
    text = render_preflight(report)
    assert "stdlib-only" in text
    assert "pip install" in text


# ─── CheckResult.status_glyph ──────────────────────────────────────────────


def test_status_glyph_required_missing() -> None:
    c = CheckResult("X", SEVERITY_REQUIRED, present=False, detected_version=None, install_hint="")
    assert c.status_glyph == "✗"


def test_status_glyph_required_present() -> None:
    c = CheckResult("X", SEVERITY_REQUIRED, present=True, detected_version="1", install_hint="")
    assert c.status_glyph == "✓"


def test_status_glyph_recommended_missing() -> None:
    c = CheckResult("X", SEVERITY_RECOMMENDED, present=False, detected_version=None, install_hint="")
    assert c.status_glyph == "!"


def test_status_glyph_optional_missing() -> None:
    c = CheckResult("X", SEVERITY_OPTIONAL, present=False, detected_version=None, install_hint="")
    assert c.status_glyph == "·"


def test_status_glyph_informational() -> None:
    c = CheckResult("X", SEVERITY_INFORMATIONAL, present=True, detected_version=None, install_hint="")
    assert c.status_glyph == "ℹ"
