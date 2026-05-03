"""Bridge module that lets init's modules import from the sync skill's scripts.

Why this exists (v1):
    init reuses sync's data model (WikiConfig, ProjectConfig, ConfigError)
    + utilities (slugify, atomic_write_json, ensure_inside, SKILL_VERSION)
    + delta-side helper (extract_frontmatter, used in init's post-render
    self-check). At v1, copying ~600 lines into init/scripts/ is worse than
    a small sys.path bootstrap. This module is the bootstrap.

Why this is OK for v1, not forever:
    Tight coupling between two skill packages is a smell. v1.x will refactor
    by lifting the shared modules to a plugin-root /lib/ directory. At that
    point this bridge module is deleted and direct `from lib.x import y`
    replaces the re-exports.

Pragmatic guarantee: every shared symbol is re-exported from this module by
explicit name, so the eventual refactor is a single global rename.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add the sync skill's scripts dir to sys.path so its modules are importable.
# Resolves to <plugin>/skills/sync/scripts.
_THIS_DIR = Path(__file__).resolve().parent
_SYNC_SCRIPTS = _THIS_DIR.parent.parent / "sync" / "scripts"
if not _SYNC_SCRIPTS.is_dir():  # defensive: should always exist in a real install
    raise RuntimeError(
        f"asof:init expected sync skill scripts at {_SYNC_SCRIPTS!s}; not found. "
        "This indicates a corrupted asof installation."
    )
if str(_SYNC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SYNC_SCRIPTS))

# Now import what we need from sync's modules.
# ruff: noqa: E402 — imports must follow the sys.path manipulation above.
from config import (  # type: ignore[import-not-found]
    CONFIG_FILENAME,
    DEFAULT_LINT_THRESHOLDS,
    DEFAULT_WIKI_DIR,
    MANDATORY_EXCLUDES,
    PATTERN_C_DIRNAME,
    SKILL_SCHEMA_VERSION,
    ConfigError,
    ProjectConfig,
    WikiConfig,
    load_wiki_config,
)
from delta import extract_frontmatter  # type: ignore[import-not-found]
from utils import (  # type: ignore[import-not-found]
    SKILL_VERSION,
    atomic_write_json,
    atomic_write_text,
    compare_versions,
    ensure_inside,
    parse_version,
    slugify,
)

__all__ = [
    # utils
    "SKILL_VERSION",
    "atomic_write_json",
    "atomic_write_text",
    "compare_versions",
    "ensure_inside",
    "parse_version",
    "slugify",
    # config
    "CONFIG_FILENAME",
    "DEFAULT_LINT_THRESHOLDS",
    "DEFAULT_WIKI_DIR",
    "MANDATORY_EXCLUDES",
    "PATTERN_C_DIRNAME",
    "SKILL_SCHEMA_VERSION",
    "ConfigError",
    "ProjectConfig",
    "WikiConfig",
    "load_wiki_config",
    # delta
    "extract_frontmatter",
]
