"""Wiki and project configuration: data model + load + validate.

Owns the immutable `WikiConfig` / `ProjectConfig` types that the rest of the
skill consumes, and the single entry point `load_wiki_config(wiki_dir)` that
parses + validates `.asof.json`.

Resolution rules (which wiki, which project, version compat, self-ingest
guard) live in `resolution.py` to keep this module focused on the data
model and IO.

Production rules:
    - Frozen dataclasses; no mutation after load.
    - Explicit error messages with the offending file + key + reason.
    - Mandatory excludes (.asof, .last-sync) enforced at load time —
      cheap insurance against Pattern C self-ingest.
    - Project-name slugification + Path.resolve containment check at load.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from utils import ensure_inside, slugify

# ─── constants ──────────────────────────────────────────────────────────────

#: Wiki-format version this skill produces and migrates to.
#: Bumped only on schema changes per PLAN.md section 17 discipline.
SKILL_SCHEMA_VERSION: str = "1.0"

#: Excludes that must always be present in every project's exclude list to
#: prevent self-ingest in Pattern C wikis. See PLAN.md C1 fix (Codex round 1).
MANDATORY_EXCLUDES: frozenset[str] = frozenset({".asof", ".last-sync"})

#: The wiki's self-describing config file lives at <wiki_dir>/.asof.json.
CONFIG_FILENAME: str = ".asof.json"

#: Pattern C marker dir: <repo-root>/.asof/ holds the wiki for a single project.
PATTERN_C_DIRNAME: str = ".asof"

#: Default location for a Pattern A wiki when no arg / env / walk-up resolves.
DEFAULT_WIKI_DIR: Path = Path.home() / ".claude" / "asof"

#: Default lint thresholds (overridable per-wiki via `lint_thresholds` block).
DEFAULT_LINT_THRESHOLDS: dict[str, int] = {
    "mtime_drift_days": 30,
    "supersession_gap_days": 60,
}


# ─── data model ─────────────────────────────────────────────────────────────


class ConfigError(ValueError):
    """Raised when `.asof.json` is malformed or violates an invariant."""


@dataclasses.dataclass(frozen=True)
class ProjectConfig:
    """Immutable view of one project entry in `.asof.json`.

    All paths are absolute and resolved (no `..`, no symlinks bypassed).
    `name` is slugified at load time. `excludes` is a tuple so it's
    hash-stable.
    """

    name: str
    source: Path
    raw_subdir: str
    wiki_subdir: str
    excludes: tuple[str, ...]

    def raw_path(self, wiki_dir: Path) -> Path:
        """Absolute path to this project's raw/ mirror under the wiki."""
        return ensure_inside(wiki_dir / self.raw_subdir, wiki_dir)

    def wiki_path(self, wiki_dir: Path) -> Path:
        """Absolute path to this project's wiki/ pages under the wiki."""
        return ensure_inside(wiki_dir / self.wiki_subdir, wiki_dir)


@dataclasses.dataclass(frozen=True)
class WikiConfig:
    """Immutable view of the wiki's `.asof.json` config + resolved location."""

    wiki_dir: Path
    schema_version: str
    min_reader_version: str
    min_writer_version: str
    lint_thresholds: dict[str, int]
    projects: tuple[ProjectConfig, ...]
    is_pattern_c: bool

    @property
    def lock_path(self) -> Path:
        return self.wiki_dir / ".asof.lock"

    @property
    def last_sync_dir(self) -> Path:
        return self.wiki_dir / ".last-sync"

    def project_by_name(self, name: str) -> ProjectConfig | None:
        for p in self.projects:
            if p.name == name:
                return p
        return None


# ─── load + validate ────────────────────────────────────────────────────────


def load_wiki_config(wiki_dir: Path | str) -> WikiConfig:
    """Load `.asof.json` from `wiki_dir` and return a validated `WikiConfig`.

    Raises:
        FileNotFoundError: if the wiki dir or config file does not exist.
        ConfigError: if the JSON is malformed or required invariants fail
                     (mandatory excludes missing, project-name not slug-safe,
                     subdirs escaping wiki_dir, etc.).
    """
    wiki_dir_path = Path(wiki_dir).resolve()
    config_path = wiki_dir_path / CONFIG_FILENAME
    if not config_path.is_file():
        raise FileNotFoundError(
            f"no asof config at {config_path!s} — run `/asof:init` to bootstrap"
        )

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{config_path!s}: invalid JSON — {exc.msg} (line {exc.lineno})"
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path!s}: top-level must be a JSON object")

    is_pattern_c = "wiki_dir" not in raw  # Pattern C omits wiki_dir
    return _build_wiki_config(raw, wiki_dir_path, is_pattern_c, config_path)


def _build_wiki_config(
    raw: dict[str, Any],
    wiki_dir_path: Path,
    is_pattern_c: bool,
    config_path: Path,
) -> WikiConfig:
    schema_version = _str_field(raw, "schema_version", config_path)
    min_reader = _str_field(raw, "min_reader_version", config_path)
    min_writer = _str_field(raw, "min_writer_version", config_path)
    lint_thresholds = _validate_lint_thresholds(
        raw.get("lint_thresholds", {}), config_path
    )
    projects_raw = raw.get("projects", [])
    if not isinstance(projects_raw, list):
        raise ConfigError(f"{config_path!s}: `projects` must be a list")
    projects = tuple(
        _build_project_config(p, wiki_dir_path, is_pattern_c, config_path, idx)
        for idx, p in enumerate(projects_raw)
    )
    # Project-name uniqueness check.
    seen: set[str] = set()
    for p in projects:
        if p.name in seen:
            raise ConfigError(
                f"{config_path!s}: project name {p.name!r} appears multiple times"
            )
        seen.add(p.name)

    return WikiConfig(
        wiki_dir=wiki_dir_path,
        schema_version=schema_version,
        min_reader_version=min_reader,
        min_writer_version=min_writer,
        lint_thresholds=lint_thresholds,
        projects=projects,
        is_pattern_c=is_pattern_c,
    )


def _build_project_config(
    raw: Any,
    wiki_dir: Path,
    is_pattern_c: bool,
    config_path: Path,
    idx: int,
) -> ProjectConfig:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{config_path!s}: projects[{idx}] must be a JSON object"
        )

    raw_name = raw.get("name")
    if not isinstance(raw_name, str):
        raise ConfigError(
            f"{config_path!s}: projects[{idx}].name must be a string"
        )
    try:
        name = slugify(raw_name)
    except ValueError as exc:
        raise ConfigError(
            f"{config_path!s}: projects[{idx}].name = {raw_name!r}: {exc}"
        ) from exc

    # `source` is required for Pattern A/B; auto-derived for Pattern C.
    if "source" in raw:
        source_str = raw["source"]
        if not isinstance(source_str, str):
            raise ConfigError(
                f"{config_path!s}: projects[{idx}].source must be a string"
            )
        source = Path(source_str).expanduser().resolve()
    elif is_pattern_c:
        # Pattern C: wiki_dir IS <repo-root>/.asof, so source = wiki_dir.parent.
        source = wiki_dir.parent
    else:
        raise ConfigError(
            f"{config_path!s}: projects[{idx}].source is required "
            f"for Pattern A/B wikis (only omittable in Pattern C)"
        )
    if not source.exists():
        raise ConfigError(
            f"{config_path!s}: projects[{idx}].source = {source!s} does not exist"
        )

    raw_subdir = _str_field(
        raw, "raw_subdir", config_path, prefix=f"projects[{idx}]."
    )
    wiki_subdir = _str_field(
        raw, "wiki_subdir", config_path, prefix=f"projects[{idx}]."
    )
    # Both subdirs must resolve INSIDE wiki_dir (path-traversal guard).
    try:
        ensure_inside(wiki_dir / raw_subdir, wiki_dir)
        ensure_inside(wiki_dir / wiki_subdir, wiki_dir)
    except ValueError as exc:
        raise ConfigError(
            f"{config_path!s}: projects[{idx}] subdir escapes wiki_dir: {exc}"
        ) from exc

    excludes_raw = raw.get("excludes", [])
    if not isinstance(excludes_raw, list) or not all(
        isinstance(e, str) for e in excludes_raw
    ):
        raise ConfigError(
            f"{config_path!s}: projects[{idx}].excludes must be a list of strings"
        )
    excludes = tuple(excludes_raw)
    missing = MANDATORY_EXCLUDES - set(excludes)
    if missing:
        raise ConfigError(
            f"{config_path!s}: projects[{idx}].excludes is missing mandatory "
            f"entries {sorted(missing)} (prevents Pattern C self-ingest). "
            f"Add them to the config and re-run."
        )

    return ProjectConfig(
        name=name,
        source=source,
        raw_subdir=raw_subdir,
        wiki_subdir=wiki_subdir,
        excludes=excludes,
    )


def _str_field(
    raw: dict[str, Any], key: str, path: Path, *, prefix: str = ""
) -> str:
    val = raw.get(key)
    if not isinstance(val, str) or not val:
        raise ConfigError(
            f"{path!s}: `{prefix}{key}` is required and must be a non-empty string"
        )
    return val


def _validate_lint_thresholds(raw: Any, path: Path) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path!s}: `lint_thresholds` must be a JSON object")
    out = dict(DEFAULT_LINT_THRESHOLDS)
    for k, v in raw.items():
        if not isinstance(v, int) or v < 0:
            raise ConfigError(
                f"{path!s}: lint_thresholds[{k!r}] must be a non-negative integer"
            )
        out[k] = v
    return out
