"""asof:init stage 1 — preflight system checks.

Verifies the user's machine has what asof needs before bootstrapping a wiki.
Fails fast on missing required tools so init never produces a partially-set-
up wiki the user later has to clean up.

Required:
    - Python 3.9+ (the runtime asof's stdlib code targets)
    - rsync (the sync skill shells out to it)

Recommended:
    - git (for versioning the wiki, especially Pattern C)

Optional:
    - Obsidian (best UX for browsing the wiki)

Explicitly NOT required (and we tell the user so they don't go installing
things they think they need):
    - uv, pip, node, npm (asof is stdlib-only at runtime)

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import sys
from pathlib import Path

#: Severity levels for preflight checks. Drives both the rendered table and
#: the "should we abort?" decision in init's main flow.
SEVERITY_REQUIRED = "required"
SEVERITY_RECOMMENDED = "recommended"
SEVERITY_OPTIONAL = "optional"
SEVERITY_INFORMATIONAL = "informational"

#: The lowest Python version asof targets. asof itself is stdlib-only, but
#: type-hint syntax (PEP 604 unions, etc.) and dataclass features assume 3.9.
MIN_PYTHON: tuple[int, int] = (3, 9)


# ─── data model ────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """Outcome of a single preflight check."""

    name: str  # display name, e.g. "Python 3.9+"
    severity: str  # SEVERITY_* constant
    present: bool  # True if the dependency was found / version satisfied
    detected_version: str | None  # e.g. "3.13.2" or None when not detected
    install_hint: str  # actionable text shown if `present` is False

    @property
    def status_glyph(self) -> str:
        """Compact symbol for the rendered table."""
        if self.severity == SEVERITY_INFORMATIONAL:
            return "ℹ"
        if self.present:
            return "✓"
        if self.severity == SEVERITY_REQUIRED:
            return "✗"  # blocking
        if self.severity == SEVERITY_RECOMMENDED:
            return "!"  # warning
        return "·"  # optional missing — informational only


@dataclasses.dataclass(frozen=True)
class PreflightReport:
    """All preflight check outcomes, plus aggregate properties."""

    checks: tuple[CheckResult, ...]

    @property
    def has_required_failure(self) -> bool:
        """True iff any REQUIRED dependency is missing."""
        return any(
            c.severity == SEVERITY_REQUIRED and not c.present for c in self.checks
        )

    @property
    def required_failures(self) -> tuple[CheckResult, ...]:
        return tuple(
            c for c in self.checks
            if c.severity == SEVERITY_REQUIRED and not c.present
        )


# ─── individual checks ─────────────────────────────────────────────────────


def check_python() -> CheckResult:
    """Verify the running Python is ≥ 3.9.

    Uses `sys.version_info` directly — we trust the interpreter we're running
    in (rather than shelling out to `python3 --version` which could resolve
    to a different binary).
    """
    major, minor = sys.version_info[:2]
    detected = f"{major}.{minor}.{sys.version_info[2]}"
    present = (major, minor) >= MIN_PYTHON
    install_hint = (
        f"Install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ via Homebrew "
        "(`brew install python@3.12`) or pyenv. asof's runtime code is "
        "stdlib-only — no pip / uv install needed once Python is present."
    )
    return CheckResult(
        name=f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+",
        severity=SEVERITY_REQUIRED,
        present=present,
        detected_version=detected,
        install_hint=install_hint,
    )


def check_rsync() -> CheckResult:
    """Verify rsync is on PATH (the sync skill shells out to it)."""
    binary = shutil.which("rsync")
    detected_version: str | None = None
    if binary:
        try:
            proc = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, check=False, timeout=5
            )
            # First line typically: "rsync  version 3.2.7  protocol version 31"
            first = proc.stdout.splitlines()[0] if proc.stdout else ""
            for token in first.split():
                if token[:1].isdigit():
                    detected_version = token
                    break
        except (OSError, subprocess.TimeoutExpired):
            detected_version = None
    return CheckResult(
        name="rsync",
        severity=SEVERITY_REQUIRED,
        present=binary is not None,
        detected_version=detected_version,
        install_hint=(
            "Install via `brew install rsync` (macOS) or your package manager "
            "(Linux: usually preinstalled; if not, `apt install rsync` / "
            "`dnf install rsync`)."
        ),
    )


def check_git() -> CheckResult:
    """Verify git is on PATH (recommended for versioning the wiki)."""
    binary = shutil.which("git")
    detected_version: str | None = None
    if binary:
        try:
            proc = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, check=False, timeout=5
            )
            # Format: "git version 2.43.0"
            parts = proc.stdout.split()
            if len(parts) >= 3:
                detected_version = parts[2]
        except (OSError, subprocess.TimeoutExpired):
            detected_version = None
    return CheckResult(
        name="git",
        severity=SEVERITY_RECOMMENDED,
        present=binary is not None,
        detected_version=detected_version,
        install_hint=(
            "git isn't required to run asof, but you'll want it to version the "
            "wiki — especially in Pattern C where the wiki travels with the "
            "source repo. Install via Xcode CLI tools (`xcode-select --install`) "
            "on macOS or your package manager."
        ),
    )


def check_obsidian() -> CheckResult:
    """Detect whether Obsidian is installed (optional UX boost)."""
    # Three platform-aware probes:
    #   macOS: /Applications/Obsidian.app
    #   PATH: `obsidian` binary (some Linux distros)
    #   PATH: `obsidian.AppImage` (typical Linux flatpak/AppImage layout)
    probes: list[Path | str] = [
        Path("/Applications/Obsidian.app"),
        "obsidian",
        "obsidian.AppImage",
    ]
    present = False
    for probe in probes:
        if isinstance(probe, Path):
            if probe.exists():
                present = True
                break
        elif shutil.which(probe):
            present = True
            break
    return CheckResult(
        name="Obsidian",
        severity=SEVERITY_OPTIONAL,
        present=present,
        detected_version=None,
        install_hint=(
            "Optional. The wiki is plain markdown so any editor works, but "
            "Obsidian provides graph view, backlinks, and frontmatter queries "
            "that make the wiki much easier to browse. Get it from obsidian.md."
        ),
    )


def informational_no_extra_deps() -> CheckResult:
    """Always-present informational note that no extra runtime deps are needed.

    Surfaces in the preflight table so users don't waste time installing tools
    they think they need (uv, pip, node, npm).
    """
    return CheckResult(
        name="Python deps / Node / uv",
        severity=SEVERITY_INFORMATIONAL,
        present=True,  # informational always renders as present
        detected_version=None,
        install_hint=(
            "asof is stdlib-only at runtime. You do NOT need pip install, "
            "uv sync, npm install, or any third-party packages to use asof. "
            "If you contribute to asof, the dev requirements (pytest, ruff) "
            "are optional and listed in pyproject.toml."
        ),
    )


# ─── orchestrator ──────────────────────────────────────────────────────────


def run_preflight() -> PreflightReport:
    """Run every preflight check in deterministic order, return a report."""
    return PreflightReport(
        checks=(
            check_python(),
            check_rsync(),
            check_git(),
            check_obsidian(),
            informational_no_extra_deps(),
        )
    )


# ─── rendering ─────────────────────────────────────────────────────────────


def render_preflight(report: PreflightReport) -> str:
    """Format the report as an aligned plain-text table for terminal output.

    Keeps columns narrow enough for an 80-column terminal in the typical case.
    Severity column is omitted from the visible row but encoded into
    `status_glyph` (✓ / ! / ✗ / · / ℹ).
    """
    name_w = max(len(c.name) for c in report.checks)
    lines = ["asof:init — preflight check"]
    lines.append("─" * (name_w + 24))
    for c in report.checks:
        version_part = f" ({c.detected_version})" if c.detected_version else ""
        lines.append(f"  {c.status_glyph} {c.name:<{name_w}}{version_part}")
    lines.append("")

    # Render install hints for anything that's missing or informational.
    notes = [c for c in report.checks if not c.present or c.severity == SEVERITY_INFORMATIONAL]
    if notes:
        lines.append("Notes:")
        for c in notes:
            lines.append(f"  • {c.name}: {c.install_hint}")
    return "\n".join(lines)
