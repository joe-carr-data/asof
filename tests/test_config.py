"""Tests for skills/sync/scripts/config.py (data model + load + validate)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from config import (
    DEFAULT_LINT_THRESHOLDS,
    MANDATORY_EXCLUDES,
    ConfigError,
    ProjectConfig,
    WikiConfig,
    load_wiki_config,
)

# ─── fixtures / builders ────────────────────────────────────────────────────


def _valid_pattern_a_config(source: Path) -> dict[str, Any]:
    """Minimal valid Pattern A config; caller writes it to <wiki_dir>/.asof.json."""
    return {
        "wiki_dir": "/will/be/ignored/at/load/time",  # load uses wiki_dir arg
        "schema_version": "1.0",
        "min_reader_version": "1.0",
        "min_writer_version": "1.0",
        "projects": [
            {
                "name": "demo",
                "source": str(source),
                "raw_subdir": "raw/demo",
                "wiki_subdir": "wiki/demo",
                "excludes": [".git", ".asof", ".last-sync"],
            }
        ],
    }


def _valid_pattern_c_config() -> dict[str, Any]:
    """Minimal valid Pattern C config (no wiki_dir, no source — both auto-derived)."""
    return {
        "schema_version": "1.0",
        "min_reader_version": "1.0",
        "min_writer_version": "1.0",
        "projects": [
            {
                "name": "myrepo",
                "raw_subdir": "raw/myrepo",
                "wiki_subdir": "wiki/myrepo",
                "excludes": [".git", ".asof", ".last-sync"],
            }
        ],
    }


def _write_config(wiki_dir: Path, data: dict) -> Path:
    wiki_dir.mkdir(parents=True, exist_ok=True)
    path = wiki_dir / ".asof.json"
    path.write_text(json.dumps(data, indent=2))
    return path


# ─── load_wiki_config: happy paths ─────────────────────────────────────────


def test_load_pattern_a(tmp_path: Path) -> None:
    source = tmp_path / "source-repo"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    _write_config(wiki_dir, _valid_pattern_a_config(source))

    cfg = load_wiki_config(wiki_dir)

    assert isinstance(cfg, WikiConfig)
    assert cfg.wiki_dir == wiki_dir.resolve()
    assert cfg.is_pattern_c is False
    assert cfg.schema_version == "1.0"
    assert cfg.min_reader_version == "1.0"
    assert cfg.min_writer_version == "1.0"
    assert cfg.lint_thresholds == DEFAULT_LINT_THRESHOLDS
    assert len(cfg.projects) == 1
    p = cfg.projects[0]
    assert p.name == "demo"
    assert p.source == source.resolve()
    assert p.raw_subdir == "raw/demo"
    assert p.wiki_subdir == "wiki/demo"
    assert ".asof" in p.excludes


def test_load_pattern_c(tmp_path: Path) -> None:
    """Pattern C: wiki_dir omitted, source auto-derived from .asof/ parent."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wiki_dir = repo / ".asof"
    _write_config(wiki_dir, _valid_pattern_c_config())

    cfg = load_wiki_config(wiki_dir)

    assert cfg.is_pattern_c is True
    assert cfg.wiki_dir == wiki_dir.resolve()
    p = cfg.projects[0]
    # Source auto-derived = parent of .asof/ = the repo root
    assert p.source == repo.resolve()


def test_load_with_custom_lint_thresholds(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    cfg_data = _valid_pattern_a_config(source)
    cfg_data["lint_thresholds"] = {"mtime_drift_days": 7, "supersession_gap_days": 14}
    _write_config(wiki_dir, cfg_data)

    cfg = load_wiki_config(wiki_dir)

    # Custom values applied; missing keys keep defaults
    assert cfg.lint_thresholds["mtime_drift_days"] == 7
    assert cfg.lint_thresholds["supersession_gap_days"] == 14


def test_load_partial_lint_thresholds_falls_back_to_defaults(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    cfg_data = _valid_pattern_a_config(source)
    cfg_data["lint_thresholds"] = {"mtime_drift_days": 99}
    _write_config(wiki_dir, cfg_data)

    cfg = load_wiki_config(wiki_dir)
    assert cfg.lint_thresholds["mtime_drift_days"] == 99
    assert (
        cfg.lint_thresholds["supersession_gap_days"]
        == DEFAULT_LINT_THRESHOLDS["supersession_gap_days"]
    )


# ─── load_wiki_config: error paths ─────────────────────────────────────────


def test_load_raises_when_no_config_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run `/asof:init`"):
        load_wiki_config(tmp_path)


def test_load_raises_on_invalid_json(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / ".asof.json").write_text("{ not valid json")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_wiki_config(wiki_dir)


def test_load_raises_when_top_level_not_object(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / ".asof.json").write_text("[]")
    with pytest.raises(ConfigError, match="top-level must be a JSON object"):
        load_wiki_config(wiki_dir)


@pytest.mark.parametrize(
    "missing_key",
    ["schema_version", "min_reader_version", "min_writer_version"],
)
def test_load_raises_when_required_field_missing(
    tmp_path: Path, missing_key: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    del data[missing_key]
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match=missing_key):
        load_wiki_config(wiki_dir)


def test_load_raises_when_mandatory_excludes_missing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data["projects"][0]["excludes"] = [".git"]  # Missing .asof and .last-sync
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="missing mandatory entries"):
        load_wiki_config(wiki_dir)


def test_load_rejects_path_traversal_in_raw_subdir(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data["projects"][0]["raw_subdir"] = "../escape"
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="escapes wiki_dir"):
        load_wiki_config(wiki_dir)


def test_load_rejects_path_traversal_in_wiki_subdir(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data["projects"][0]["wiki_subdir"] = "../escape"
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="escapes wiki_dir"):
        load_wiki_config(wiki_dir)


def test_load_slugifies_project_name(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data["projects"][0]["name"] = "My Cool Project"
    _write_config(wiki_dir, data)
    cfg = load_wiki_config(wiki_dir)
    assert cfg.projects[0].name == "my-cool-project"


def test_load_rejects_unsafe_project_name(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data["projects"][0]["name"] = "../etc"
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="path separators"):
        load_wiki_config(wiki_dir)


def test_load_rejects_duplicate_project_names(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data["projects"].append(
        {
            "name": "demo",  # duplicate!
            "source": str(source),
            "raw_subdir": "raw/demo2",
            "wiki_subdir": "wiki/demo2",
            "excludes": [".asof", ".last-sync"],
        }
    )
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="appears multiple times"):
        load_wiki_config(wiki_dir)


def test_load_pattern_a_requires_source(tmp_path: Path) -> None:
    """In Pattern A (wiki_dir present), source is required."""
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(tmp_path)
    del data["projects"][0]["source"]
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="source is required"):
        load_wiki_config(wiki_dir)


def test_load_raises_when_source_missing(tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(tmp_path / "does-not-exist")
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="does not exist"):
        load_wiki_config(wiki_dir)


def test_load_raises_on_malformed_excludes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data["projects"][0]["excludes"] = "not-a-list"
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="excludes must be a list"):
        load_wiki_config(wiki_dir)


def test_load_raises_on_negative_lint_threshold(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data["lint_thresholds"] = {"mtime_drift_days": -1}
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError, match="non-negative integer"):
        load_wiki_config(wiki_dir)


@pytest.mark.parametrize(
    "version_field",
    ["schema_version", "min_reader_version", "min_writer_version"],
)
@pytest.mark.parametrize("bad_value", ["abc", "1.x.0", "1..0", ""])
def test_load_raises_on_malformed_version_string(
    tmp_path: Path, version_field: str, bad_value: str
) -> None:
    """Codex round-1 phase-1 M2: invalid version strings must surface as
    ConfigError at load time, not as an uncaught ValueError later in
    check_version_compat."""
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    data = _valid_pattern_a_config(source)
    data[version_field] = bad_value
    _write_config(wiki_dir, data)
    with pytest.raises(ConfigError):
        load_wiki_config(wiki_dir)


# ─── ProjectConfig + WikiConfig methods ─────────────────────────────────────


def test_project_config_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    _write_config(wiki_dir, _valid_pattern_a_config(source))
    cfg = load_wiki_config(wiki_dir)
    p = cfg.projects[0]

    raw = p.raw_path(cfg.wiki_dir)
    wiki = p.wiki_path(cfg.wiki_dir)
    assert raw == (cfg.wiki_dir / "raw/demo").resolve()
    assert wiki == (cfg.wiki_dir / "wiki/demo").resolve()


def test_wiki_config_project_by_name(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    _write_config(wiki_dir, _valid_pattern_a_config(source))
    cfg = load_wiki_config(wiki_dir)
    assert cfg.project_by_name("demo") is not None
    assert cfg.project_by_name("nonexistent") is None


def test_wiki_config_lock_and_last_sync_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    _write_config(wiki_dir, _valid_pattern_a_config(source))
    cfg = load_wiki_config(wiki_dir)
    assert cfg.lock_path == cfg.wiki_dir / ".asof.lock"
    assert cfg.last_sync_dir == cfg.wiki_dir / ".last-sync"


def test_wiki_config_is_frozen(tmp_path: Path) -> None:
    """Frozen dataclass: assignment after construction must raise."""
    source = tmp_path / "source"
    source.mkdir()
    wiki_dir = tmp_path / "wiki"
    _write_config(wiki_dir, _valid_pattern_a_config(source))
    cfg = load_wiki_config(wiki_dir)
    with pytest.raises(dataclasses_FrozenInstanceError):
        cfg.schema_version = "9.9"  # type: ignore[misc]


# ─── helpers ────────────────────────────────────────────────────────────────


# `dataclasses.FrozenInstanceError` lives in `dataclasses` since 3.7
import dataclasses  # noqa: E402

dataclasses_FrozenInstanceError = dataclasses.FrozenInstanceError


# ─── sanity: mandatory excludes constant ────────────────────────────────────


def test_mandatory_excludes_includes_self_ingest_guards() -> None:
    """The two excludes that prevent Pattern C self-ingest must be mandatory."""
    assert ".asof" in MANDATORY_EXCLUDES
    assert ".last-sync" in MANDATORY_EXCLUDES


def test_project_config_is_immutable(tmp_path: Path) -> None:
    p = ProjectConfig(
        name="demo",
        source=tmp_path,
        raw_subdir="raw/demo",
        wiki_subdir="wiki/demo",
        excludes=(".asof", ".last-sync"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.name = "x"  # type: ignore[misc]
