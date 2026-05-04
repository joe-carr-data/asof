"""init → lint chain: a freshly bootstrapped wiki must lint clean.

Phase 5 cross-phase integration. Each test runs init AND lint as real
subprocesses and asserts (a) init exits 0, (b) lint exits 0 with no
findings, (c) the .asof.json schema/version fields are correct, and
(d) no machine-local absolute paths leaked into pages that should be
relative-only (which would break Phase 6 example wikis on CI).
"""

from __future__ import annotations

import json
from pathlib import Path

from .conftest import run_skill


def _read_config(wiki_dir: Path) -> dict:
    return json.loads((wiki_dir / ".asof.json").read_text(encoding="utf-8"))


# ─── Pattern A ─────────────────────────────────────────────────────────────


def test_pattern_a_init_then_lint_clean(pattern_a_wiki: Path) -> None:
    """Pattern A: shared wiki under home. Lint must report Wiki is clean."""
    result = run_skill("lint", ["--wiki-dir", str(pattern_a_wiki)])
    assert result.returncode == 0, result.stderr
    assert "Wiki is clean" in result.stdout
    assert "0 errors" in result.stdout


def test_pattern_a_init_writes_skill_versioned_floors(pattern_a_wiki: Path) -> None:
    """Init must write SKILL_VERSION (not schema_version) into the
    min_reader/min_writer floors so the same plugin can sync the wiki
    immediately. Round-1 phase-3 CRITICAL regression check."""
    cfg = _read_config(pattern_a_wiki)
    assert cfg["min_reader_version"] == cfg["min_writer_version"]
    # And NOT equal to schema_version (the floors track skill, not schema).
    assert cfg["min_reader_version"] != cfg["schema_version"]


def test_pattern_a_init_includes_wiki_dir_and_source(pattern_a_wiki: Path) -> None:
    cfg = _read_config(pattern_a_wiki)
    assert "wiki_dir" in cfg  # Pattern A includes wiki_dir
    assert cfg["projects"][0].get("source")  # Pattern A projects include source


# ─── Pattern C ─────────────────────────────────────────────────────────────


def test_pattern_c_init_then_lint_clean(pattern_c_wiki: Path) -> None:
    """Pattern C: in-repo .asof/. Lint must report clean."""
    result = run_skill("lint", ["--wiki-dir", str(pattern_c_wiki)])
    assert result.returncode == 0, result.stderr
    assert "Wiki is clean" in result.stdout


def test_pattern_c_committed_config_omits_machine_local_paths(
    pattern_c_wiki: Path,
) -> None:
    """Pattern C config travels portably across forks/clones — it MUST
    NOT include `wiki_dir` (auto-derived at load time) and the project
    block MUST NOT include `source` (auto-derived as wiki_dir.parent).
    Phase 6 example wikis depend on this for CI portability."""
    cfg = _read_config(pattern_c_wiki)
    assert "wiki_dir" not in cfg
    assert cfg["projects"]
    assert "source" not in cfg["projects"][0]


def test_pattern_c_walks_up_to_find_wiki(pattern_c_wiki: Path) -> None:
    """Pattern C: lint can find the wiki by walking up from a deeper cwd
    inside the repo, no --wiki-dir flag needed. This is the canonical
    user flow for in-repo wikis."""
    repo = pattern_c_wiki.parent
    nested = repo / "src" / "deep" / "nested"
    nested.mkdir(parents=True)
    result = run_skill("lint", cwd=nested)
    assert result.returncode == 0, result.stderr
    assert "Wiki is clean" in result.stdout


# ─── invalid-config gate (subprocess-level) ────────────────────────────────


def test_lint_halts_on_corrupt_asof_json(pattern_a_wiki: Path) -> None:
    """Round-1 phase-3 HIGH 3 contract held end-to-end via subprocess."""
    (pattern_a_wiki / ".asof.json").write_text("{not json", encoding="utf-8")
    result = run_skill("lint", ["--wiki-dir", str(pattern_a_wiki)])
    assert result.returncode == 4, result.stderr
    assert "invalid config" in result.stderr
    assert "untrusted config" in result.stderr


def test_lint_unknown_project_returns_4(pattern_a_wiki: Path) -> None:
    result = run_skill(
        "lint", ["does-not-exist", "--wiki-dir", str(pattern_a_wiki)]
    )
    assert result.returncode == 4, result.stderr
    assert "unknown project" in result.stderr


# ─── --json output is parseable end-to-end ─────────────────────────────────


def test_lint_json_output_is_parseable(pattern_a_wiki: Path) -> None:
    result = run_skill(
        "lint", ["--wiki-dir", str(pattern_a_wiki), "--json"]
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["wiki_dir"] == str(pattern_a_wiki)
    assert payload["summary"] == {"errors": 0, "warnings": 0, "info": 0}
    assert isinstance(payload["projects"], list)


def test_lint_severity_filter_changes_exit_code(
    pattern_a_wiki: Path,
) -> None:
    """Inject an orphan-page (INFO finding) and verify --severity warn
    drops it from the report → exit 0; --severity info keeps it → exit 1."""
    project_dir = pattern_a_wiki / "wiki" / "myproj"
    (project_dir / "entities").mkdir(parents=True, exist_ok=True)
    (project_dir / "entities" / "orphan.md").write_text(
        "---\ntitle: O\ntype: entity\nproject: myproj\nlast_updated: 2026-05-04\n---\n",
        encoding="utf-8",
    )
    info_result = run_skill(
        "lint", ["--wiki-dir", str(pattern_a_wiki), "--severity", "info"]
    )
    assert info_result.returncode == 1, info_result.stderr
    warn_result = run_skill(
        "lint", ["--wiki-dir", str(pattern_a_wiki), "--severity", "warn"]
    )
    assert warn_result.returncode == 0, warn_result.stderr
