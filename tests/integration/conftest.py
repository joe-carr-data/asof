"""Shared subprocess fixtures for cross-phase integration tests.

Phase 5: tests in this directory invoke init / sync / lint as real
subprocesses (not direct internal calls) so the full CLI surface is
exercised — argparse, exit codes, file locks, sys.path bootstraps.

The helpers here are deliberately minimal — they call `python3
<script>` rather than installing the plugin, so the tests can run
against the working tree without a build step.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_BY_SKILL: dict[str, Path] = {
    "init": REPO_ROOT / "skills" / "init" / "scripts" / "init.py",
    "sync": REPO_ROOT / "skills" / "sync" / "scripts" / "sync.py",
    "lint": REPO_ROOT / "skills" / "lint" / "scripts" / "lint.py",
}

#: Env vars that asof's skills read from the parent environment. Stripped
#: by default in `run_skill()` so tests are hermetic — a developer with
#: ASOF_DIR set in their shell would otherwise leak it into every child
#: process and break cwd-resolution tests. Codex round-1 phase-5 HIGH;
#: ASOF_NON_INTERACTIVE added round-2 (sync.py + wizard.py read it).
_ASOF_ENV_VARS: tuple[str, ...] = (
    "ASOF_DIR",
    "ASOF_NON_INTERACTIVE",
    "ASOF_SKILL_VERSION_OVERRIDE",
)


@dataclasses.dataclass(frozen=True)
class SkillResult:
    """Captured stdout/stderr/exit code from a single skill invocation."""

    returncode: int
    stdout: str
    stderr: str

    def assert_success(self, message: str = "") -> None:
        if self.returncode != 0:
            extra = f" — {message}" if message else ""
            raise AssertionError(
                f"Expected exit 0{extra}; got {self.returncode}\n"
                f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}"
            )


def run_skill(
    skill: str,
    args: Iterable[str] = (),
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 30.0,
) -> SkillResult:
    """Invoke a skill's main script as a real subprocess.

    `skill` is one of "init", "sync", "lint". `args` is the CLI argv
    AFTER the script path. `cwd` is the working directory for the
    subprocess (matters for sync's project auto-resolve and for lint's
    walk-up wiki resolution). `env` is merged onto os.environ — pass
    {"ASOF_DIR": ...} to test env-var resolution.

    Returns a SkillResult with captured outputs. Does NOT raise on
    non-zero exit (caller decides what to assert).
    """
    if skill not in SCRIPTS_BY_SKILL:
        raise ValueError(
            f"unknown skill {skill!r}; expected one of {list(SCRIPTS_BY_SKILL)}"
        )
    script = SCRIPTS_BY_SKILL[skill]
    # Inherit parent env, then SCRUB asof-specific vars so a developer
    # shell with ASOF_DIR set doesn't pollute the subprocess. Tests that
    # explicitly want to set ASOF_DIR pass it via `env=`; an empty string
    # is preserved (sync's resolver treats "" as "not set" via truthy
    # check, which is documented behavior we exercise in the resolution
    # tests). Codex round-1 phase-5 HIGH.
    full_env = dict(os.environ)
    for var in _ASOF_ENV_VARS:
        full_env.pop(var, None)
    if env is not None:
        full_env.update({k: str(v) for k, v in env.items()})
    proc = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd) if cwd else None,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return SkillResult(
        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )


@pytest.fixture
def pattern_a_wiki(tmp_path: Path) -> Path:
    """Bootstrap a Pattern A wiki via init's real subprocess.

    Returns the wiki_dir absolute path. Source repo is at <tmp_path>/src
    and contains no markdown files yet — tests can populate it.
    """
    source = tmp_path / "src"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    result = run_skill(
        "init",
        [
            "myproj",
            str(source),
            "--pattern", "A",
            "--wiki-dir", str(wiki_dir),
            "--non-interactive",
            "--skip-first-sync",
            "--no-install-hook",
            "--no-claudemd-snippet",
            "--no-additional-directories",
        ],
    )
    result.assert_success("Pattern A bootstrap")
    return wiki_dir


@pytest.fixture
def pattern_c_wiki(tmp_path: Path) -> Path:
    """Bootstrap a Pattern C wiki (in-repo .asof/) via init's real subprocess.

    Returns the wiki_dir, which is `<tmp_path>/repo/.asof/`. The source
    repo is at <tmp_path>/repo/ and the wiki lives inside it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    result = run_skill(
        "init",
        [
            "myrepo",
            str(repo),
            "--pattern", "C",
            "--non-interactive",
            "--skip-first-sync",
            "--no-install-hook",
            "--no-claudemd-snippet",
            "--no-additional-directories",
        ],
    )
    result.assert_success("Pattern C bootstrap")
    return repo / ".asof"
