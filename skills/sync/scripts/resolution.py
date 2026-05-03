"""Wiki-dir resolution, project selection, version compat, self-ingest guard.

The runtime "policy" layer that turns CLI args + cwd + the loaded
`WikiConfig` into "operate on these projects, with this compat verdict".

Split out from `config.py` (which owns the data model + load) so each module
stays under 400 lines and has a single concern.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import enum
import os
from pathlib import Path

from config import (
    CONFIG_FILENAME,
    DEFAULT_WIKI_DIR,
    PATTERN_C_DIRNAME,
    SKILL_SCHEMA_VERSION,
    ConfigError,
    ProjectConfig,
    WikiConfig,
)
from utils import SKILL_VERSION, compare_versions, slugify

# ─── version-compat result types ────────────────────────────────────────────


class CompatStatus(enum.Enum):
    """Outcome of the four-cell schema-version compatibility matrix."""

    ALLOWED = "allowed"  # cell (d): work normally
    READ_ONLY = "read_only"  # cell (b): lint OK, sync/init/migrate blocked
    REQUIRE_MIGRATE = "require_migrate"  # cell (c): need explicit --migrate
    REFUSE = "refuse"  # cell (a): skill too old, must upgrade


@dataclasses.dataclass(frozen=True)
class CompatResult:
    """Compat-matrix outcome plus a human-readable explanation."""

    status: CompatStatus
    message: str


class ProjectSelectionError(ValueError):
    """Raised when project selection cannot be unambiguously resolved."""


# ─── wiki_dir resolution (the four-step chain from PLAN.md section 4) ──────


def resolve_wiki_dir(
    arg: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Resolve which wiki dir to use for the current invocation.

    Order, first match wins:
        1. Explicit `arg` (--wiki-dir CLI flag).
        2. `ASOF_DIR` env var.
        3. Walk up from `cwd` looking for `.asof/` (Pattern C) or `.asof.json`.
        4. `~/.claude/asof/` default (Pattern A).

    Returns an absolute, resolved Path. Does NOT verify the wiki is
    initialized (caller does that via `load_wiki_config`).

    Args:
        arg: --wiki-dir CLI value, if provided.
        env: dict to read env from (default: os.environ). Test seam.
        cwd: starting dir for walk-up (default: Path.cwd()). Test seam.
    """
    env = env if env is not None else os.environ
    cwd = (cwd or Path.cwd()).resolve()

    if arg:
        return Path(arg).expanduser().resolve()
    if asof_env := env.get("ASOF_DIR"):
        return Path(asof_env).expanduser().resolve()

    # Walk up from cwd looking for a Pattern C marker.
    for candidate in (cwd, *cwd.parents):
        # Pattern C: `<candidate>/.asof/.asof.json`
        pattern_c = candidate / PATTERN_C_DIRNAME / CONFIG_FILENAME
        if pattern_c.is_file():
            return (candidate / PATTERN_C_DIRNAME).resolve()
        # Bare config: `<candidate>/.asof.json` (Pattern A/B with this dir as wiki_dir)
        bare = candidate / CONFIG_FILENAME
        if bare.is_file():
            return candidate.resolve()

    return DEFAULT_WIKI_DIR.resolve()


# ─── project resolution (cwd-aware auto-select) ────────────────────────────


def resolve_projects(
    config: WikiConfig,
    *,
    name: str | None = None,
    all_projects: bool = False,
    cwd: Path | None = None,
    non_interactive: bool = False,
    auto_select_longest: bool = False,
) -> list[ProjectConfig]:
    """Pick which configured project(s) to operate on.

    Resolution rules (PLAN.md section 4):
        - `--all` (`all_projects=True`)            → every configured project
        - `--project <name>` (`name=...`)          → that exact project, or error
        - cwd inside exactly one project's source  → auto-select
        - cwd inside multiple projects (nested):
            * interactive                → caller prompts (we return all matches)
            * non-interactive without
              `auto_select_longest`      → fail-fast
            * non-interactive with
              `auto_select_longest`      → pick deepest (longest-path) match
        - cwd inside no projects                   → fail-fast
    """
    if all_projects:
        return list(config.projects)

    if name:
        slug = slugify(name)
        proj = config.project_by_name(slug)
        if proj is None:
            available = ", ".join(p.name for p in config.projects) or "(none)"
            raise ProjectSelectionError(
                f"no project named {slug!r} in {config.wiki_dir!s}. "
                f"Available: {available}."
            )
        return [proj]

    cwd = (cwd or Path.cwd()).resolve()
    matches = [p for p in config.projects if _is_inside(cwd, p.source)]

    if not matches:
        names = (
            ", ".join(p.name for p in config.projects)
            or "(no projects configured)"
        )
        raise ProjectSelectionError(
            f"current directory {cwd!s} is not inside any configured "
            f"project's source. Specify --project <name> or --all. "
            f"Available: {names}."
        )

    if len(matches) == 1:
        return matches

    # Multi-match (nested sources)
    if auto_select_longest:
        # Pick the deepest (most specific) match. Equal-length names: stable order.
        deepest = max(matches, key=lambda p: len(p.source.parts))
        return [deepest]
    if non_interactive:
        names = ", ".join(p.name for p in matches)
        raise ProjectSelectionError(
            f"cwd {cwd!s} matches multiple projects: {names}. "
            f"In non-interactive mode, specify --project <name>, --all, "
            f"or --auto-select-longest."
        )
    # Interactive: caller will prompt; return all matches as candidates.
    return list(matches)


def _is_inside(child: Path, parent: Path) -> bool:
    """True if `child` is `parent` or a descendant of `parent`."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


# ─── version compatibility (the four-cell matrix) ──────────────────────────


def check_version_compat(
    config: WikiConfig,
    *,
    skill_version: str = SKILL_VERSION,
    skill_schema_version: str = SKILL_SCHEMA_VERSION,
) -> CompatResult:
    """Apply the four-cell schema-version compatibility matrix (PLAN section 2).

    Cells:
        (a) skill_version < min_reader_version → REFUSE
        (b) min_reader ≤ skill < min_writer    → READ_ONLY
        (c) skill ≥ min_writer AND
            wiki_schema < skill_schema         → REQUIRE_MIGRATE
        (d) skill ≥ min_writer AND
            wiki_schema ≥ skill_schema         → ALLOWED (no migration needed)

    Args:
        config: loaded WikiConfig.
        skill_version: override for testing (defaults to runtime SKILL_VERSION).
        skill_schema_version: override for testing.
    """
    if compare_versions(skill_version, config.min_reader_version) < 0:
        return CompatResult(
            status=CompatStatus.REFUSE,
            message=(
                f"This wiki requires asof ≥ {config.min_reader_version} but "
                f"this skill is {skill_version}. Upgrade asof to continue."
            ),
        )
    if compare_versions(skill_version, config.min_writer_version) < 0:
        return CompatResult(
            status=CompatStatus.READ_ONLY,
            message=(
                f"asof {skill_version} can read this wiki (schema "
                f"{config.schema_version}) but cannot write to it. Upgrade "
                f"to ≥ {config.min_writer_version} for sync / init / migrate."
            ),
        )
    if compare_versions(config.schema_version, skill_schema_version) < 0:
        return CompatResult(
            status=CompatStatus.REQUIRE_MIGRATE,
            message=(
                f"Wiki schema is {config.schema_version}; asof {skill_version} "
                f"writes schema {skill_schema_version}. Run with --migrate to "
                f"upgrade the wiki, or downgrade asof to {config.schema_version}."
            ),
        )
    return CompatResult(
        status=CompatStatus.ALLOWED,
        message=(
            f"asof {skill_version} compatible with wiki schema "
            f"{config.schema_version} (no migration needed)"
        ),
    )


# ─── self-ingest guard ─────────────────────────────────────────────────────


def check_self_ingest_safe(project: ProjectConfig, wiki_dir: Path) -> None:
    """Raise ConfigError if a sync would recurse into the wiki itself.

    Pattern C wikis live at `<source>/.asof/`. If `.asof` is missing from the
    project's excludes, rsync would mirror `<source>/.asof/raw/` *into itself*,
    producing recursive bloat. We already require `.asof` in excludes via
    `MANDATORY_EXCLUDES` at config-load time, but this is a belt-and-suspenders
    runtime check before rsync starts.
    """
    try:
        wiki_inside_source = _is_inside(
            wiki_dir.resolve(), project.source.resolve()
        )
    except OSError:
        wiki_inside_source = False
    if not wiki_inside_source:
        return
    # wiki_dir is inside source — verify .asof is excluded
    if PATTERN_C_DIRNAME not in project.excludes:
        raise ConfigError(
            f"refusing to sync: wiki_dir {wiki_dir!s} is inside source "
            f"{project.source!s} and {PATTERN_C_DIRNAME!r} is missing from "
            f"excludes. This would recurse into the wiki itself. Add "
            f"{PATTERN_C_DIRNAME!r} to the project's excludes list."
        )
