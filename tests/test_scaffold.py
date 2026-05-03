"""Tests for skills/init/scripts/scaffold.py — stages 3 + 4 of the wizard.

Covers template loading + rendering + post-render verification, wiki-dir
bootstrap, .asof.json registration (Pattern A/B vs Pattern C), per-project
page scaffolding, and the do_scaffold orchestrator. Dry-run paths are
asserted to leave the filesystem untouched.

The plugin's actual templates/ directory is used (loaded via
resolve_template_dir()) so these tests double as a sanity check that the
shipped templates render cleanly with the standard substitution map.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _sync_bridge import MANDATORY_EXCLUDES, load_wiki_config
from scaffold import (
    DEFAULT_EXCLUDES,
    PROJECT_SUBDIRS,
    PROJECT_TEMPLATES,
    ROOT_CLAUDE_TEMPLATE,
    ScaffoldError,
    ScaffoldRequest,
    ScaffoldResult,
    WikiLayout,
    _all_default_excludes_include_mandatory,
    bootstrap_wiki_dir,
    do_scaffold,
    load_template,
    register_project_in_config,
    render_template,
    resolve_template_dir,
    scaffold_project_pages,
    verify_frontmatter_ok,
    verify_substituted,
)

# ─── invariants ────────────────────────────────────────────────────────────


def test_default_excludes_include_all_mandatory_excludes() -> None:
    """Defense-in-depth: DEFAULT_EXCLUDES must contain every MANDATORY_EXCLUDES
    entry. Future edits that drop `.asof` or `.last-sync` would let Pattern C
    self-ingest, so this invariant is enforced at module-import time AND
    tested explicitly here."""
    assert _all_default_excludes_include_mandatory()
    assert MANDATORY_EXCLUDES.issubset(set(DEFAULT_EXCLUDES))


def test_project_templates_map_covers_four_bookkeeping_files() -> None:
    """SCHEMA.md §7 enumerates four bookkeeping files (index, log,
    _candidates, current_state). PROJECT_TEMPLATES must map them all."""
    targets = set(PROJECT_TEMPLATES.values())
    assert targets == {"index.md", "log.md", "_candidates.md", "current_state.md"}


def test_project_subdirs_match_schema() -> None:
    """SCHEMA.md §4: per-project subdirs are entities/, concepts/, sources/."""
    assert set(PROJECT_SUBDIRS) == {"entities", "concepts", "sources"}


# ─── WikiLayout invariants ─────────────────────────────────────────────────


def test_wiki_layout_pattern_a_requires_source(tmp_path: Path) -> None:
    """Pattern A/B carry an explicit `source`; missing source raises."""
    with pytest.raises(ValueError, match="requires an explicit source"):
        WikiLayout(pattern="A", wiki_dir=tmp_path / "wiki", source=None)


def test_wiki_layout_pattern_b_requires_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires an explicit source"):
        WikiLayout(pattern="B", wiki_dir=tmp_path / "wiki", source=None)


def test_wiki_layout_pattern_c_forbids_explicit_source(tmp_path: Path) -> None:
    """Pattern C derives source from wiki_dir.parent; passing source raises."""
    with pytest.raises(ValueError, match="do not pass an explicit source"):
        WikiLayout(
            pattern="C",
            wiki_dir=tmp_path / ".asof",
            source=tmp_path,
        )


def test_wiki_layout_pattern_c_accepts_no_source(tmp_path: Path) -> None:
    layout = WikiLayout(pattern="C", wiki_dir=tmp_path / ".asof")
    assert layout.source is None
    assert layout.pattern == "C"


# ─── template loading ──────────────────────────────────────────────────────


def test_resolve_template_dir_finds_real_templates() -> None:
    """The shipped templates/ dir must be resolvable from this file's
    location. Sanity-checks plugin layout."""
    template_dir = resolve_template_dir()
    assert template_dir.is_dir()
    # Verify all the bookkeeping templates and the root CLAUDE template exist
    for tpl in PROJECT_TEMPLATES:
        assert (template_dir / tpl).is_file(), f"missing {tpl}"
    assert (template_dir / ROOT_CLAUDE_TEMPLATE).is_file()


def test_load_template_returns_text() -> None:
    text = load_template("wiki_index.md")
    assert "{{PROJECT_NAME}}" in text  # placeholder confirms it's the template


def test_load_template_missing_raises() -> None:
    with pytest.raises(ScaffoldError, match="not found"):
        load_template("does_not_exist.md")


# ─── render_template ───────────────────────────────────────────────────────


def test_render_template_substitutes_keys() -> None:
    out = render_template(
        "Hello {{NAME}}, welcome to {{PLACE}}!",
        {"NAME": "World", "PLACE": "asof"},
    )
    assert out == "Hello World, welcome to asof!"


def test_render_template_handles_repeated_placeholder() -> None:
    out = render_template("{{X}} and {{X}} again", {"X": "foo"})
    assert out == "foo and foo again"


def test_render_template_leaves_unsubstituted_placeholders_alone() -> None:
    out = render_template("{{KEEP}} but {{REPLACE}}", {"REPLACE": "ok"})
    assert "{{KEEP}}" in out
    assert "ok" in out


def test_render_template_pure_function_no_io(tmp_path: Path) -> None:
    """render_template must not touch the filesystem."""
    files_before = list(tmp_path.iterdir())
    render_template("anything {{X}}", {"X": "y"})
    files_after = list(tmp_path.iterdir())
    assert files_before == files_after


# ─── verify_substituted / verify_frontmatter_ok ────────────────────────────


def test_verify_substituted_passes_when_no_placeholders(tmp_path: Path) -> None:
    """No leftovers → no error."""
    verify_substituted("clean text\nno placeholders", tmp_path / "x.md")


def test_verify_substituted_raises_on_leftover_placeholder(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError, match="unsubstituted placeholders"):
        verify_substituted(
            "rendered with {{LEFTOVER}} bug", tmp_path / "x.md"
        )


def test_verify_substituted_lists_unique_leftovers(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError) as exc:
        verify_substituted(
            "{{A}} and {{B}} and {{A}} again", tmp_path / "x.md"
        )
    msg = str(exc.value)
    assert "{{A}}" in msg and "{{B}}" in msg


def test_verify_frontmatter_ok_passes_for_valid(tmp_path: Path) -> None:
    rendered = "---\ntitle: x\ntype: overview\n---\n\nbody"
    verify_frontmatter_ok(rendered, tmp_path / "x.md")  # no error


def test_verify_frontmatter_ok_raises_for_no_fence(tmp_path: Path) -> None:
    with pytest.raises(ScaffoldError, match="frontmatter"):
        verify_frontmatter_ok("# Just a heading\n", tmp_path / "x.md")


# ─── stage 3: bootstrap_wiki_dir ───────────────────────────────────────────


def _make_layout_a(tmp_path: Path) -> WikiLayout:
    source = tmp_path / "src"
    source.mkdir()
    return WikiLayout(pattern="A", wiki_dir=tmp_path / "wiki", source=source)


def _make_layout_c(tmp_path: Path) -> WikiLayout:
    repo = tmp_path / "repo"
    repo.mkdir()
    return WikiLayout(pattern="C", wiki_dir=repo / ".asof")


def test_bootstrap_creates_wiki_dir_structure(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    created_dir, created, _, _ = bootstrap_wiki_dir(
        layout, dry_run=False, today="2026-05-04"
    )
    assert created_dir is True
    assert layout.wiki_dir.is_dir()
    assert (layout.wiki_dir / "raw").is_dir()
    assert (layout.wiki_dir / "wiki").is_dir()
    claude = layout.wiki_dir / "CLAUDE.md"
    assert claude in created
    assert claude.is_file()


def test_bootstrap_substitutes_root_claude_placeholders(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    bootstrap_wiki_dir(layout, dry_run=False, today="2026-05-04")
    text = (layout.wiki_dir / "CLAUDE.md").read_text()
    assert "2026-05-04" in text
    assert str(layout.wiki_dir) in text
    assert "{{TODAY}}" not in text
    assert "{{WIKI_DIR}}" not in text
    assert "{{ASOF_VERSION}}" not in text


def test_bootstrap_skips_existing_claude(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    (layout.wiki_dir / "CLAUDE.md").write_text("# pre-existing")
    _, created, updated, skipped = bootstrap_wiki_dir(
        layout, dry_run=False, today="2026-05-04"
    )
    assert layout.wiki_dir / "CLAUDE.md" in skipped
    assert layout.wiki_dir / "CLAUDE.md" not in created


def test_bootstrap_dry_run_no_writes(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    bootstrap_wiki_dir(layout, dry_run=True, today="2026-05-04")
    assert not layout.wiki_dir.exists()


# ─── stage 3: register_project_in_config ───────────────────────────────────


def test_register_writes_fresh_pattern_a_config(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    config_path, created_new = register_project_in_config(
        layout, "myproject", dry_run=False
    )
    assert created_new is True
    cfg = json.loads(config_path.read_text())
    # Pattern A includes wiki_dir
    assert cfg["wiki_dir"] == str(layout.wiki_dir)
    assert cfg["schema_version"] == "1.0"
    # Project block includes source for Pattern A
    proj = cfg["projects"][0]
    assert proj["name"] == "myproject"
    assert proj["source"] == str(layout.source)
    assert proj["raw_subdir"] == "raw/myproject"
    assert proj["wiki_subdir"] == "wiki/myproject"
    # Default excludes include the mandatory ones
    for mandatory in MANDATORY_EXCLUDES:
        assert mandatory in proj["excludes"]


def test_register_writes_skill_version_for_compat_floor(tmp_path: Path) -> None:
    """Codex round-1 phase-3 CRITICAL fix: min_reader_version /
    min_writer_version must be the current SKILL version, not the
    SCHEMA version. Previous bug wrote SCHEMA "1.0" into these fields,
    which the same plugin (running as v0.1.0-dev) then refused to sync."""
    from _sync_bridge import SKILL_VERSION

    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    config_path, _ = register_project_in_config(
        layout, "myproject", dry_run=False
    )
    cfg = json.loads(config_path.read_text())
    # The fields are skill versions, not schema versions
    assert cfg["min_reader_version"] == SKILL_VERSION
    assert cfg["min_writer_version"] == SKILL_VERSION
    # schema_version is still the wiki-format version
    assert cfg["schema_version"] == "1.0"


def test_init_to_sync_compat_round_trip(tmp_path: Path) -> None:
    """End-to-end: an init'd wiki must satisfy sync's compat matrix when
    operated on by the SAME plugin version. Cell (d) — newer-or-equal skill
    + newer-or-equal schema → ALLOWED.

    Codex round-1 phase-3 CRITICAL: this test would have failed against
    the previous code (skill 0.1.0-dev < min_reader 1.0 → REFUSE)."""
    from _sync_bridge import load_wiki_config
    from resolution import CompatStatus, check_version_compat

    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "myproject", dry_run=False)

    cfg = load_wiki_config(layout.wiki_dir)
    result = check_version_compat(cfg)
    assert result.status == CompatStatus.ALLOWED, (
        f"Fresh init wiki must be compat-ALLOWED with the writing skill, "
        f"got {result.status} ({result.message})"
    )


def test_register_writes_fresh_pattern_c_config(tmp_path: Path) -> None:
    """Pattern C: committed config omits wiki_dir AND project's source."""
    layout = _make_layout_c(tmp_path)
    layout.wiki_dir.mkdir()
    config_path, _ = register_project_in_config(
        layout, "myproject", dry_run=False
    )
    cfg = json.loads(config_path.read_text())
    assert "wiki_dir" not in cfg
    proj = cfg["projects"][0]
    assert "source" not in proj
    assert proj["name"] == "myproject"


def test_register_appends_to_existing_config(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "first", dry_run=False)
    register_project_in_config(layout, "second", dry_run=False)
    cfg = json.loads((layout.wiki_dir / ".asof.json").read_text())
    names = {p["name"] for p in cfg["projects"]}
    assert names == {"first", "second"}


def test_register_refuses_duplicate_project(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "myproject", dry_run=False)
    with pytest.raises(ScaffoldError, match="already exists"):
        register_project_in_config(layout, "myproject", dry_run=False)


def test_register_dry_run_no_write(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "myproject", dry_run=True)
    assert not (layout.wiki_dir / ".asof.json").exists()


def test_register_produces_loadable_config(tmp_path: Path) -> None:
    """The .asof.json written by init must be loadable by sync's
    load_wiki_config — round-trip integration check."""
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "myproject", dry_run=False)
    cfg = load_wiki_config(layout.wiki_dir)
    assert cfg.projects[0].name == "myproject"
    assert cfg.is_pattern_c is False


def test_register_pattern_c_config_loadable(tmp_path: Path) -> None:
    layout = _make_layout_c(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "myrepo", dry_run=False)
    cfg = load_wiki_config(layout.wiki_dir)
    assert cfg.is_pattern_c is True
    assert cfg.projects[0].source == layout.wiki_dir.parent


# ─── round-1 phase-3 HIGH 3: validate existing .asof.json before mutate ───


def test_register_refuses_malformed_existing_config(tmp_path: Path) -> None:
    """If `<wiki_dir>/.asof.json` is corrupt JSON, register raises a
    ScaffoldError with a helpful pointer instead of silently overwriting
    or producing a cryptic ConfigError.

    Codex round-1 phase-3 HIGH 3: previous code used raw json.loads and
    bypassed every invariant in load_wiki_config — a hand-edited config
    with broken JSON would just blow up with json.JSONDecodeError mid-init."""
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    (layout.wiki_dir / ".asof.json").write_text(
        "{not even json", encoding="utf-8"
    )
    with pytest.raises(ScaffoldError, match="invalid"):
        register_project_in_config(layout, "myproject", dry_run=False)


def test_register_refuses_existing_config_missing_version(tmp_path: Path) -> None:
    """An existing config missing required version fields fails ConfigError
    inside load_wiki_config — translated to a ScaffoldError so the init
    user sees a coherent error path."""
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    (layout.wiki_dir / ".asof.json").write_text(
        json.dumps(
            {
                "wiki_dir": str(layout.wiki_dir),
                # schema_version intentionally missing
                "projects": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScaffoldError, match="invalid"):
        register_project_in_config(layout, "myproject", dry_run=False)


def test_register_preserves_forward_compat_keys(tmp_path: Path) -> None:
    """Re-reading the raw dict (after load_wiki_config validation) preserves
    keys WikiConfig doesn't model — important so unknown forward-compat
    keys don't get silently stripped on second-project append."""
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "first", dry_run=False)
    # Inject a hypothetical forward-compat key into the config
    cfg_path = layout.wiki_dir / ".asof.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["future_field"] = {"hello": "world"}
    cfg_path.write_text(json.dumps(raw), encoding="utf-8")
    # Second project append — must preserve future_field
    register_project_in_config(layout, "second", dry_run=False)
    final = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert final.get("future_field") == {"hello": "world"}
    assert {p["name"] for p in final["projects"]} == {"first", "second"}


# ─── round-1 phase-3 HIGH 4: Pattern C is single-project ──────────────────


def test_register_pattern_c_refuses_second_project(tmp_path: Path) -> None:
    """Pattern C wikis live at <repo>/.asof/ — one repo, one project. A
    second project would silently change the layout's invariants and
    produce a config that the sync skill assumes is single-project.

    Codex round-1 phase-3 HIGH 4: PLAN.md §4 makes this explicit but
    init wasn't enforcing it."""
    layout = _make_layout_c(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "myrepo", dry_run=False)
    with pytest.raises(ScaffoldError, match="single-project"):
        register_project_in_config(layout, "another-project", dry_run=False)


def test_register_pattern_a_allows_multiple_projects(tmp_path: Path) -> None:
    """Pattern A is the multi-project layout — second register must succeed.
    Sanity check that the Pattern C single-project guard didn't bleed
    into the Pattern A path."""
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    register_project_in_config(layout, "first", dry_run=False)
    register_project_in_config(layout, "second", dry_run=False)
    cfg = json.loads((layout.wiki_dir / ".asof.json").read_text())
    assert {p["name"] for p in cfg["projects"]} == {"first", "second"}


# ─── stage 4: scaffold_project_pages ───────────────────────────────────────


def test_scaffold_renders_all_four_bookkeeping_pages(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    request = ScaffoldRequest(
        layout=layout,
        project_display_name="My Project",
        project_slug="my-project",
    )
    created, _ = scaffold_project_pages(request, today="2026-05-04", dry_run=False)
    project_dir = layout.wiki_dir / "wiki" / "my-project"
    for tgt in PROJECT_TEMPLATES.values():
        assert project_dir / tgt in created
        assert (project_dir / tgt).is_file()


def test_scaffold_creates_subdirs(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    request = ScaffoldRequest(layout, "X", "x")
    scaffold_project_pages(request, today="2026-05-04", dry_run=False)
    project_dir = layout.wiki_dir / "wiki" / "x"
    for sub in PROJECT_SUBDIRS:
        assert (project_dir / sub).is_dir()


def test_scaffold_substitutes_all_placeholders(tmp_path: Path) -> None:
    """No `{{...}}` should survive substitution on any rendered page —
    the verify_substituted check inside scaffold_project_pages enforces this."""
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    request = ScaffoldRequest(layout, "My Project", "my-project")
    scaffold_project_pages(request, today="2026-05-04", dry_run=False)
    for tgt in PROJECT_TEMPLATES.values():
        content = (layout.wiki_dir / "wiki" / "my-project" / tgt).read_text()
        # No leftover placeholders
        assert "{{" not in content, f"leftover {{...}} in {tgt}"
        # And the substitution worked
        if tgt == "current_state.md":
            assert "My Project" in content
            assert "my-project" in content
            assert "2026-05-04" in content


def test_scaffold_skips_existing_pages(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    request = ScaffoldRequest(layout, "X", "x")
    project_dir = layout.wiki_dir / "wiki" / "x"
    project_dir.mkdir(parents=True)
    pre = project_dir / "index.md"
    pre.write_text("# pre-existing index")
    created, skipped = scaffold_project_pages(
        request, today="2026-05-04", dry_run=False
    )
    assert pre in skipped
    assert pre not in created
    # Pre-existing content must NOT have been overwritten
    assert pre.read_text() == "# pre-existing index"


def test_scaffold_dry_run_no_writes(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    layout.wiki_dir.mkdir()
    request = ScaffoldRequest(layout, "X", "x")
    scaffold_project_pages(request, today="2026-05-04", dry_run=True)
    project_dir = layout.wiki_dir / "wiki" / "x"
    # Project dir wasn't created in dry-run (caller wouldn't expect it)
    assert not project_dir.exists()


def test_scaffold_request_rejects_unslugified_value() -> None:
    """ScaffoldRequest validates project_slug round-trips through slugify
    (idempotent on already-slug values). `../escape` would normalize to
    `escape` and not round-trip, so construction raises immediately —
    before any filesystem write."""
    layout = WikiLayout(pattern="A", wiki_dir=Path("/tmp/x"), source=Path("/tmp/y"))
    with pytest.raises(ValueError, match="not a valid slug"):
        ScaffoldRequest(layout, "Evil", "../escape")


@pytest.mark.parametrize(
    "bad_slug",
    [
        "../escape",
        "foo/bar",
        "Upper",
        "with spaces",
        "trailing-",
        "-leading",
        "",
    ],
)
def test_scaffold_request_rejects_various_unsafe_slugs(bad_slug: str) -> None:
    layout = WikiLayout(pattern="A", wiki_dir=Path("/tmp/x"), source=Path("/tmp/y"))
    with pytest.raises(ValueError):
        ScaffoldRequest(layout, "Display", bad_slug)


def test_scaffold_request_accepts_valid_slug() -> None:
    layout = WikiLayout(pattern="A", wiki_dir=Path("/tmp/x"), source=Path("/tmp/y"))
    # Should not raise
    req = ScaffoldRequest(layout, "Display", "my-project-1")
    assert req.project_slug == "my-project-1"


# ─── orchestrator: do_scaffold ─────────────────────────────────────────────


def test_do_scaffold_end_to_end_pattern_a(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    request = ScaffoldRequest(layout, "Demo", "demo")
    result = do_scaffold(request, today="2026-05-04", dry_run=False)

    assert isinstance(result, ScaffoldResult)
    assert result.dry_run is False
    assert result.wiki_dir_created is True
    # Must have created CLAUDE.md, the four bookkeeping files, and ".asof.json"
    created_names = {p.name for p in result.files_created}
    assert "CLAUDE.md" in created_names
    assert "index.md" in created_names
    assert "log.md" in created_names
    assert "_candidates.md" in created_names
    assert "current_state.md" in created_names
    # .asof.json registered as updated (it's the wiki dir's central config)
    updated_names = {p.name for p in result.files_updated}
    assert ".asof.json" in updated_names


def test_do_scaffold_end_to_end_pattern_c(tmp_path: Path) -> None:
    layout = _make_layout_c(tmp_path)
    request = ScaffoldRequest(layout, "MyRepo", "myrepo")
    result = do_scaffold(request, today="2026-05-04", dry_run=False)

    cfg = load_wiki_config(layout.wiki_dir)
    assert cfg.is_pattern_c is True
    assert cfg.projects[0].name == "myrepo"
    assert cfg.projects[0].source == layout.wiki_dir.parent
    assert result.dry_run is False


def test_do_scaffold_dry_run_creates_nothing(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    request = ScaffoldRequest(layout, "Demo", "demo")
    result = do_scaffold(request, today="2026-05-04", dry_run=True)
    assert result.dry_run is True
    # Wiki dir was never created
    assert not layout.wiki_dir.exists()


def test_do_scaffold_idempotent_re_run_skips_existing_pages(
    tmp_path: Path,
) -> None:
    """Re-running with the SAME slug must refuse via the duplicate-project
    check, but if a user somehow runs scaffold_project_pages directly twice
    the existing pages are skipped, not overwritten. Tested separately
    above; here we verify the orchestrator's first-run shape."""
    layout = _make_layout_a(tmp_path)
    request = ScaffoldRequest(layout, "Demo", "demo")
    do_scaffold(request, today="2026-05-04", dry_run=False)

    # Re-running with the same slug must raise via the duplicate check
    with pytest.raises(ScaffoldError, match="already exists"):
        do_scaffold(request, today="2026-05-04", dry_run=False)


# ─── .gitignore augmentation (Pattern C) — Codex round-1 phase-3 HIGH ─────


def test_pattern_a_no_gitignore_augmentation(tmp_path: Path) -> None:
    """Pattern A wiki is outside the source repo. Don't touch source's
    .gitignore."""
    from scaffold import augment_pattern_c_gitignore

    layout = _make_layout_a(tmp_path)
    augmented, skipped = augment_pattern_c_gitignore(layout, dry_run=False)
    assert augmented is False
    assert skipped is False
    # No .gitignore was touched anywhere
    assert not (layout.source / ".gitignore").exists()


def test_pattern_c_creates_gitignore_when_missing(tmp_path: Path) -> None:
    from scaffold import (
        GITIGNORE_CLOSE_MARKER,
        GITIGNORE_OPEN_MARKER,
        PATTERN_C_GITIGNORE_ENTRIES,
        augment_pattern_c_gitignore,
    )

    layout = _make_layout_c(tmp_path)
    augmented, skipped = augment_pattern_c_gitignore(layout, dry_run=False)
    assert augmented is True
    assert skipped is False
    # source = wiki_dir.parent for Pattern C; .gitignore lives there
    gitignore = layout.wiki_dir.parent / ".gitignore"
    assert gitignore.is_file()
    text = gitignore.read_text()
    assert GITIGNORE_OPEN_MARKER in text
    assert GITIGNORE_CLOSE_MARKER in text
    for entry in PATTERN_C_GITIGNORE_ENTRIES:
        assert entry in text


def test_pattern_c_appends_to_existing_gitignore(tmp_path: Path) -> None:
    from scaffold import (
        GITIGNORE_OPEN_MARKER,
        augment_pattern_c_gitignore,
    )

    layout = _make_layout_c(tmp_path)
    gitignore = layout.wiki_dir.parent / ".gitignore"
    gitignore.write_text("node_modules/\n*.log\n")
    augmented, skipped = augment_pattern_c_gitignore(layout, dry_run=False)
    assert augmented is True
    assert skipped is False
    text = gitignore.read_text()
    # Existing content preserved
    assert "node_modules/" in text
    assert "*.log" in text
    # asof block appended after a blank-line separator
    assert GITIGNORE_OPEN_MARKER in text
    assert text.index("node_modules/") < text.index(GITIGNORE_OPEN_MARKER)


def test_pattern_c_gitignore_idempotent(tmp_path: Path) -> None:
    """Re-running augment_pattern_c_gitignore detects the marker and skips."""
    from scaffold import (
        GITIGNORE_OPEN_MARKER,
        augment_pattern_c_gitignore,
    )

    layout = _make_layout_c(tmp_path)
    augment_pattern_c_gitignore(layout, dry_run=False)
    # Second call: detects marker, skips
    augmented2, skipped2 = augment_pattern_c_gitignore(layout, dry_run=False)
    assert augmented2 is False
    assert skipped2 is True
    # Block appears only once
    text = (layout.wiki_dir.parent / ".gitignore").read_text()
    assert text.count(GITIGNORE_OPEN_MARKER) == 1


def test_pattern_c_gitignore_dry_run_no_write(tmp_path: Path) -> None:
    from scaffold import augment_pattern_c_gitignore

    layout = _make_layout_c(tmp_path)
    augmented, _ = augment_pattern_c_gitignore(layout, dry_run=True)
    assert augmented is True
    assert not (layout.wiki_dir.parent / ".gitignore").exists()


def test_do_scaffold_pattern_c_augments_gitignore(tmp_path: Path) -> None:
    """End-to-end via the orchestrator: Pattern C init augments .gitignore."""
    layout = _make_layout_c(tmp_path)
    request = ScaffoldRequest(layout, "MyRepo", "myrepo")
    result = do_scaffold(request, today="2026-05-04", dry_run=False)
    assert result.gitignore_augmented is True
    assert result.gitignore_already_done is False
    assert (layout.wiki_dir.parent / ".gitignore").is_file()


def test_do_scaffold_pattern_a_no_gitignore_change(tmp_path: Path) -> None:
    layout = _make_layout_a(tmp_path)
    request = ScaffoldRequest(layout, "Demo", "demo")
    result = do_scaffold(request, today="2026-05-04", dry_run=False)
    assert result.gitignore_augmented is False
    assert result.gitignore_already_done is False
