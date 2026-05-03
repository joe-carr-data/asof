"""Bridge module that lets lint's modules import from the sync skill's scripts.

Why this exists (v1):
    lint reuses sync's data model (WikiConfig, ProjectConfig, ConfigError)
    + utilities (file_lock, slugify, ensure_inside, atomic_write_text,
    SKILL_VERSION, version-compat helpers) + frontmatter parser
    (extract_frontmatter, parse_frontmatter_yaml). Copying these into
    lint/scripts/ would mean the lint and sync drift; a small sys.path
    bootstrap is the v1 pragmatic choice.

Why this is OK for v1, not forever:
    Init has the same bridge. v1.x lifts the shared modules to a
    plugin-root /lib/ and deletes both bridges; until then every
    re-exported symbol is named explicitly so the eventual rename
    is mechanical.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add the sync skill's scripts dir to sys.path so its modules are importable.
_THIS_DIR = Path(__file__).resolve().parent
_SYNC_SCRIPTS = _THIS_DIR.parent.parent / "sync" / "scripts"
if not _SYNC_SCRIPTS.is_dir():  # defensive: should always exist in a real install
    raise RuntimeError(
        f"asof:lint expected sync skill scripts at {_SYNC_SCRIPTS!s}; not found. "
        "This indicates a corrupted asof installation."
    )
if str(_SYNC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SYNC_SCRIPTS))

# Now import what we need from sync's modules.
# ruff: noqa: E402 — imports must follow the sys.path manipulation above.
from config import (  # type: ignore[import-not-found]
    CONFIG_FILENAME,
    DEFAULT_LINT_THRESHOLDS,
    MANDATORY_EXCLUDES,
    PATTERN_C_DIRNAME,
    SKILL_SCHEMA_VERSION,
    ConfigError,
    ProjectConfig,
    WikiConfig,
    load_wiki_config,
)
from delta import extract_frontmatter, parse_sources  # type: ignore[import-not-found]
from resolution import CompatStatus, check_version_compat  # type: ignore[import-not-found]
from utils import (  # type: ignore[import-not-found]
    SKILL_VERSION,
    atomic_write_text,
    compare_versions,
    ensure_inside,
    file_lock,
    parse_version,
    slugify,
)

__all__ = [
    # utils
    "SKILL_VERSION",
    "atomic_write_text",
    "compare_versions",
    "ensure_inside",
    "file_lock",
    "parse_version",
    "slugify",
    # config
    "CONFIG_FILENAME",
    "DEFAULT_LINT_THRESHOLDS",
    "MANDATORY_EXCLUDES",
    "PATTERN_C_DIRNAME",
    "SKILL_SCHEMA_VERSION",
    "ConfigError",
    "ProjectConfig",
    "WikiConfig",
    "load_wiki_config",
    # delta
    "extract_frontmatter",
    "parse_sources",
    # resolution
    "CompatStatus",
    "check_version_compat",
]
