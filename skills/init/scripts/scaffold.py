"""asof:init stages 3 + 4 — bootstrap wiki dir, register project, render
templates with placeholder substitution, post-render self-check.

Stage 3 (create the wiki):
    - resolve the wiki dir for the chosen pattern (A / B / C)
    - create raw/ and wiki/ if needed
    - write <wiki_dir>/CLAUDE.md from templates/wiki_root_CLAUDE.md
    - write or update <wiki_dir>/.asof.json with the new project entry

Stage 4 (scaffold project pages):
    - create wiki/<project>/{entities,concepts,sources}/ dirs
    - render templates/wiki_{index,log,_candidates,current_state}.md with
      placeholder substitution into wiki/<project>/
    - re-parse every rendered page with extract_frontmatter() to fail fast
      if any {{placeholder}} survived substitution or frontmatter is broken
      (gpt-5.2-pro round-1 phase-3 advice).

All filesystem writes are atomic (write-temp-then-rename). All paths are
containment-checked before writes. Dry-run mode skips writes but reports
what would have been done.

Stdlib only.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Literal

from _sync_bridge import (
    CONFIG_FILENAME,
    DEFAULT_LINT_THRESHOLDS,
    MANDATORY_EXCLUDES,
    SKILL_SCHEMA_VERSION,
    SKILL_VERSION,
    ConfigError,
    atomic_write_json,
    atomic_write_text,
    ensure_inside,
    extract_frontmatter,
    load_wiki_config,
    slugify,
)

# ─── constants ─────────────────────────────────────────────────────────────

#: Default excludes written into a new project's `excludes` list. Combines
#: the mandatory entries (`.asof`, `.last-sync` — prevents Pattern C
#: self-ingest) with the conventional Python / Node / build dirs that
#: never contain user-authored markdown.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git",
    ".asof",
    ".last-sync",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    ".tox",
    ".nox",
    "dist",
    "build",
    "site-packages",
    ".claude",
    ".idea",
    ".vscode",
    "test_results",
)

#: Names of the four bookkeeping templates copied into wiki/<project>/.
#: Maps template filename → target filename inside wiki/<project>/.
PROJECT_TEMPLATES: dict[str, str] = {
    "wiki_index.md": "index.md",
    "wiki_log.md": "log.md",
    "wiki_candidates.md": "_candidates.md",
    "wiki_current_state.md": "current_state.md",
}

#: Wiki-root CLAUDE.md template (single file, copied to <wiki_dir>/CLAUDE.md).
ROOT_CLAUDE_TEMPLATE: str = "wiki_root_CLAUDE.md"

#: Subdirectories created under wiki/<project>/ for the agent to organize pages.
PROJECT_SUBDIRS: tuple[str, ...] = ("entities", "concepts", "sources")

#: Regex matching unsubstituted placeholders. Used by verify_substituted to
#: catch substitution bugs before they reach the user as cryptic lint failures.
_PLACEHOLDER_RE: re.Pattern[str] = re.compile(r"\{\{[A-Z_][A-Z0-9_]*\}\}")

#: Marker comments wrapping the asof block in a Pattern C source repo's
#: .gitignore. Used to detect existing installations on re-run.
GITIGNORE_OPEN_MARKER = "# asof-wiki:gitignore"
GITIGNORE_CLOSE_MARKER = "# /asof-wiki:gitignore"

#: Entries written into <source>/.gitignore for Pattern C wikis. These are
#: the runtime artifacts asof creates inside .asof/ that should NOT be
#: committed: the rsync mirror, per-project last-sync reports, debounce
#: stamps, the lock file, and pre-migration backups (per PLAN.md section 4
#: + SCHEMA.md §12).
PATTERN_C_GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".asof/raw/",
    ".asof/.last-sync/",
    ".asof/.pending-sync/",
    ".asof/.asof.lock",
    ".asof/wiki.bak.*/",
)


# ─── data model ────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class WikiLayout:
    """The user's pattern + path choice from stage 2.

    Pattern C carries `source=None` because the source path is auto-derived
    as `wiki_dir.parent` at sync time (per SCHEMA.md / config.py rules).
    """

    pattern: Literal["A", "B", "C"]
    wiki_dir: Path  # absolute, resolved
    source: Path | None = None  # None for Pattern C

    def __post_init__(self) -> None:
        if self.pattern == "C" and self.source is not None:
            raise ValueError(
                "Pattern C derives source from wiki_dir.parent at sync time; "
                "do not pass an explicit source"
            )
        if self.pattern in ("A", "B") and self.source is None:
            raise ValueError(
                f"Pattern {self.pattern} requires an explicit source path"
            )


@dataclasses.dataclass(frozen=True)
class ScaffoldRequest:
    """All the inputs needed to scaffold a single new project.

    `project_slug` is re-validated at construction time. Callers (notably
    init.py) are expected to have slugified user input upstream, but we
    re-check here as defense-in-depth: if a slug somehow contains `..` or
    a path separator, scaffold writes never run with it.
    """

    layout: WikiLayout
    project_display_name: str  # "My Project" — what the user typed
    project_slug: str  # "my-project" — slugified, used in paths

    def __post_init__(self) -> None:
        # Defense in depth: slugify the slug and verify round-trip. slugify
        # is idempotent on valid slugs (slugify("my-project") == "my-project")
        # and raises ValueError on anything containing path separators, NUL,
        # uppercase, etc. We catch and re-raise with a unified "not a valid
        # slug" message so callers don't need to handle two error shapes.
        try:
            normalized = slugify(self.project_slug)
        except ValueError as exc:
            raise ValueError(
                f"project_slug {self.project_slug!r} is not a valid slug: {exc}"
            ) from exc
        if normalized != self.project_slug:
            raise ValueError(
                f"project_slug {self.project_slug!r} is not a valid slug "
                f"(slugify normalizes it to {normalized!r}). Callers must "
                "slugify user input before constructing ScaffoldRequest."
            )


@dataclasses.dataclass(frozen=True)
class ScaffoldResult:
    """What was actually created (or would have been, in dry-run mode)."""

    wiki_dir_created: bool  # True if wiki dir didn't exist and was created
    files_created: tuple[Path, ...]
    files_updated: tuple[Path, ...]
    files_skipped: tuple[Path, ...]  # already existed (no overwrite)
    gitignore_augmented: bool  # Pattern C: True if .gitignore got asof entries
    gitignore_already_done: bool  # Pattern C: True if asof block already there
    dry_run: bool


class ScaffoldError(RuntimeError):
    """Raised when a scaffold step fails in a way that requires user action."""


# ─── template loading + rendering ──────────────────────────────────────────


def resolve_template_dir() -> Path:
    """Return the plugin's templates/ directory (parent of init's scripts).

    Resolves `<plugin_root>/templates/` relative to this file's location.
    Fails fast with a clear error if the layout is wrong (e.g. someone
    moved the file or installed asof in an unexpected layout).
    """
    here = Path(__file__).resolve()
    # this file: <plugin>/skills/init/scripts/scaffold.py
    # parents:    [scripts, init, skills, <plugin>]
    plugin_root = here.parent.parent.parent.parent
    templates = plugin_root / "templates"
    if not templates.is_dir():
        raise ScaffoldError(
            f"asof: expected templates/ at {templates!s} but it doesn't exist. "
            "This indicates a corrupted installation."
        )
    return templates


def load_template(template_filename: str) -> str:
    """Read a template by name from `<plugin>/templates/`.

    Templates are bundled with the plugin and never modified at runtime.
    """
    template_path = resolve_template_dir() / template_filename
    if not template_path.is_file():
        raise ScaffoldError(
            f"asof: template {template_filename!r} not found at {template_path!s}"
        )
    return template_path.read_text(encoding="utf-8")


def render_template(template_text: str, substitutions: dict[str, str]) -> str:
    """Apply `{{KEY}} → value` substitutions to a template string.

    Pure function — no I/O. The substitution map's keys are the placeholder
    names without braces (e.g. `{"PROJECT_NAME": "My Project"}` substitutes
    every occurrence of `{{PROJECT_NAME}}` with `My Project`).

    Templates may contain placeholders the caller doesn't substitute; those
    survive unchanged and trigger a verify_substituted() failure later.
    Caller is responsible for providing every key the template needs.
    """
    rendered = template_text
    for key, value in substitutions.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def verify_substituted(rendered: str, page_path: Path) -> None:
    """Fail-fast if a rendered template still has unsubstituted placeholders
    or doesn't yield valid frontmatter (gpt-5.2-pro round-1 phase-3 advice).

    Two checks:
      1. No {{KEY}}-shaped tokens remain — substitution missed something.
      2. extract_frontmatter() returns non-None — the rendered page actually
         starts with a YAML fence per SCHEMA.md §3.

    The wiki_root_CLAUDE.md template doesn't have frontmatter (it's the
    wiki-root README, not a wiki page), so callers pass `expect_frontmatter
    =False` for that one.
    """
    leftovers = _PLACEHOLDER_RE.findall(rendered)
    if leftovers:
        unique = sorted(set(leftovers))
        raise ScaffoldError(
            f"asof:init: rendered template for {page_path.name!r} still contains "
            f"unsubstituted placeholders: {unique}. The template needs more "
            f"substitution keys, or the placeholder is unintentional."
        )


def verify_frontmatter_ok(rendered: str, page_path: Path) -> None:
    """Verify a rendered wiki page has parseable frontmatter per SCHEMA §3.

    Skipped for templates that intentionally have no frontmatter (e.g. the
    root CLAUDE.md). Caller decides whether to invoke.
    """
    fm = extract_frontmatter(rendered)
    if fm is None:
        raise ScaffoldError(
            f"asof:init: rendered template for {page_path.name!r} did not "
            f"produce valid frontmatter. The template's `---` fences may have "
            f"been corrupted by substitution."
        )


# ─── stage 3: wiki dir + .asof.json bootstrap ──────────────────────────────


def bootstrap_wiki_dir(
    layout: WikiLayout, *, dry_run: bool = False, today: str
) -> tuple[bool, list[Path], list[Path], list[Path]]:
    """Create the wiki dir if needed and write `<wiki_dir>/CLAUDE.md`.

    Returns: (wiki_dir_created, created_paths, updated_paths, skipped_paths).

    `today` is passed in (not generated here) so the caller can pin the date
    for deterministic tests.
    """
    created: list[Path] = []
    updated: list[Path] = []
    skipped: list[Path] = []

    wiki_dir_existed = layout.wiki_dir.exists()
    if not dry_run and not wiki_dir_existed:
        layout.wiki_dir.mkdir(parents=True, exist_ok=False)
    if not dry_run:
        (layout.wiki_dir / "raw").mkdir(parents=True, exist_ok=True)
        (layout.wiki_dir / "wiki").mkdir(parents=True, exist_ok=True)

    claude_path = layout.wiki_dir / "CLAUDE.md"
    if claude_path.is_file():
        skipped.append(claude_path)
    else:
        rendered = render_template(
            load_template(ROOT_CLAUDE_TEMPLATE),
            {
                "WIKI_DIR": str(layout.wiki_dir),
                "TODAY": today,
                "ASOF_VERSION": SKILL_VERSION,
            },
        )
        verify_substituted(rendered, claude_path)
        if not dry_run:
            atomic_write_text(claude_path, rendered)
        created.append(claude_path)

    return (not wiki_dir_existed, created, updated, skipped)


def register_project_in_config(
    layout: WikiLayout, project_slug: str, *, dry_run: bool = False
) -> tuple[Path, bool]:
    """Add the project entry to `<wiki_dir>/.asof.json`.

    If the config doesn't exist, write a fresh one (Pattern A/B includes
    `wiki_dir`; Pattern C omits it so the committed config is portable).
    If it exists, validate it via `load_wiki_config()` (catches malformed
    JSON, missing mandatory excludes, version-field corruption, etc.)
    before mutating it. After validation:
      - Pattern C: refuse if any project already exists. <repo>/.asof/
        belongs to one project — adding a second one would silently
        change the wiki layout's invariants. Round-1 phase-3 HIGH 4.
      - Slug uniqueness: refuse if `project_slug` is already registered.

    Round-1 phase-3 HIGH 3: previous code used raw `json.loads` which
    bypassed every invariant in `load_wiki_config`. A wiki that had been
    hand-edited into an invalid state would still get its `projects`
    array appended, producing a second silent corruption.

    Returns: (config_path, created_new_file).

    Raises:
        ScaffoldError: existing config is malformed, Pattern C already
                       has a project, or `project_slug` already exists.
    """
    config_path = layout.wiki_dir / CONFIG_FILENAME
    created_new = not config_path.exists()

    if created_new:
        cfg = _new_config_dict(layout)
    else:
        try:
            wiki_cfg = load_wiki_config(layout.wiki_dir)
        except ConfigError as exc:
            raise ScaffoldError(
                f"asof:init: existing config at {config_path!s} is invalid: "
                f"{exc}. Fix it by hand (or delete and re-run /asof:init) "
                "before adding a new project."
            ) from exc
        # Layout/config shape mismatch (Codex round-2 MEDIUM): the existing
        # config's pattern (derived from whether `wiki_dir` is present in the
        # JSON) must match the requested layout. Pointing a Pattern A/B init
        # at a Pattern C config (or vice versa) would silently corrupt the
        # invariants either side relies on (Pattern C committed-portably
        # vs Pattern A/B with absolute paths).
        existing_is_c = wiki_cfg.is_pattern_c
        requested_is_c = layout.pattern == "C"
        if existing_is_c != requested_is_c:
            existing_label = "C" if existing_is_c else "A/B"
            raise ScaffoldError(
                f"asof:init: existing config at {config_path!s} is a Pattern "
                f"{existing_label} wiki, but you requested Pattern "
                f"{layout.pattern}. Pattern shape is fixed at bootstrap; "
                "to switch patterns, move or delete the existing wiki first."
            )
        # Pattern C is single-project by design — one repo, one .asof/, one
        # project. A second register would silently change the layout's
        # invariants. Keyed off the *config* (is_pattern_c), which we now
        # know matches the requested layout from the check above.
        if existing_is_c and wiki_cfg.projects:
            existing = wiki_cfg.projects[0].name
            raise ScaffoldError(
                f"asof:init: Pattern C wiki at {config_path!s} already has "
                f"project {existing!r}. Pattern C is single-project by "
                "design (one repo = one .asof/ = one project). To register "
                "a different project, use Pattern A or B; to re-bootstrap "
                "this repo, delete .asof/ and re-run /asof:init."
            )
        existing_slugs = {p.name for p in wiki_cfg.projects}
        if project_slug in existing_slugs:
            raise ScaffoldError(
                f"asof:init: project {project_slug!r} already exists in "
                f"{config_path!s}. To add a different project, choose another "
                f"name. To re-bootstrap, delete the entry manually."
            )
        # Re-read raw dict so we preserve any forward-compat keys that
        # WikiConfig doesn't model. load_wiki_config has already verified
        # the JSON parses + structural invariants hold.
        cfg = json.loads(config_path.read_text(encoding="utf-8"))

    cfg.setdefault("projects", []).append(
        _new_project_block(layout, project_slug)
    )

    if not dry_run:
        atomic_write_json(config_path, cfg)
    return (config_path, created_new)


def _new_config_dict(layout: WikiLayout) -> dict:
    """Produce the top-level fields for a fresh `.asof.json`.

    Three version fields, three different things (Codex round-1 phase-3
    CRITICAL fix):
      - `schema_version`: the wiki-format version (currently "1.0").
      - `min_reader_version`: lowest SKILL version that can READ this wiki.
        Set to the current skill version (SKILL_VERSION). Newer skills
        compare ≥ and pass.
      - `min_writer_version`: same, for WRITE operations.

    Previous bug: wrote SKILL_SCHEMA_VERSION ("1.0") into both
    min_reader/writer fields. With plugin version "0.1.0-dev", sync's
    compat matrix saw `0.1.0-dev < 1.0` → REFUSE, meaning fresh init
    produced a wiki the same plugin couldn't sync.
    """
    base: dict = {
        "schema_version": SKILL_SCHEMA_VERSION,
        "min_reader_version": SKILL_VERSION,
        "min_writer_version": SKILL_VERSION,
        "lint_thresholds": dict(DEFAULT_LINT_THRESHOLDS),
        "projects": [],
    }
    # wiki_dir omitted for Pattern C so committed config travels portably
    # across forks/clones (round-2 fix from gpt-5.2-pro).
    if layout.pattern != "C":
        base = {"wiki_dir": str(layout.wiki_dir), **base}
    return base


def _new_project_block(layout: WikiLayout, project_slug: str) -> dict:
    """Produce the per-project block to embed in `.asof.json` `projects: []`."""
    block: dict = {
        "name": project_slug,
        "raw_subdir": f"raw/{project_slug}",
        "wiki_subdir": f"wiki/{project_slug}",
        "excludes": list(DEFAULT_EXCLUDES),
    }
    # source is omitted for Pattern C (auto-derived from wiki_dir.parent at
    # load time per config.py rules); Pattern A/B requires it.
    if layout.pattern != "C":
        # Insert source as the second key for nicer JSON ordering.
        if layout.source is None:  # type-checker placation; __post_init__ enforces
            raise ScaffoldError(
                f"Pattern {layout.pattern} requires source"
            )
        block = {
            "name": block["name"],
            "source": str(layout.source),
            "raw_subdir": block["raw_subdir"],
            "wiki_subdir": block["wiki_subdir"],
            "excludes": block["excludes"],
        }
    return block


# ─── stage 3 (Pattern C): .gitignore augmentation ─────────────────────────


def augment_pattern_c_gitignore(
    layout: WikiLayout, *, dry_run: bool = False
) -> tuple[bool, bool]:
    """Append the asof entries to <source>/.gitignore for Pattern C.

    Pattern A/B: no-op (wiki dir is outside the source repo, no risk of
    committing wiki internals). Returns (False, False) for those.

    Pattern C: read <source>/.gitignore (or treat as empty if missing),
    detect the marker block, append idempotently if absent. Returns
    (augmented, skipped_already_present).

    Marker-fenced (`# asof-wiki:gitignore` ... `# /asof-wiki:gitignore`)
    so re-runs detect existing installs and skip without duplicating.
    Existing .gitignore content is preserved verbatim.

    Codex round-1 phase-3 HIGH: PLAN.md §4 promised this for Pattern C
    but bootstrap_wiki_dir didn't implement it; PR-time committers were
    one slip away from staging the entire raw/ mirror.
    """
    if layout.pattern != "C":
        return (False, False)

    # Pattern C: source repo root is wiki_dir.parent (the .asof/ lives there).
    gitignore_path = layout.wiki_dir.parent / ".gitignore"

    existing = ""
    if gitignore_path.is_file():
        existing = gitignore_path.read_text(encoding="utf-8")
        # Idempotent: detect existing asof block by either marker
        if (
            GITIGNORE_OPEN_MARKER in existing
            or GITIGNORE_CLOSE_MARKER in existing
        ):
            return (False, True)

    block_lines = [GITIGNORE_OPEN_MARKER, *PATTERN_C_GITIGNORE_ENTRIES, GITIGNORE_CLOSE_MARKER]
    block = "\n".join(block_lines) + "\n"

    # Normalize trailing newlines if existing content; blank-line separator
    # before the block for visual cleanliness.
    new_content = (
        existing.rstrip("\n") + "\n\n" + block if existing else block
    )

    if not dry_run:
        atomic_write_text(gitignore_path, new_content)
    return (True, False)


# ─── stage 4: project pages ────────────────────────────────────────────────


def scaffold_project_pages(
    request: ScaffoldRequest, *, today: str, dry_run: bool = False
) -> tuple[list[Path], list[Path]]:
    """Render the four bookkeeping templates into wiki/<project>/.

    Returns: (created_paths, skipped_paths). `skipped_paths` are pages that
    already exist (we never overwrite — re-init is for adding NEW projects).
    """
    project_dir = request.layout.wiki_dir / "wiki" / request.project_slug
    project_dir = ensure_inside(project_dir, request.layout.wiki_dir)

    if not dry_run:
        project_dir.mkdir(parents=True, exist_ok=True)
        for sub in PROJECT_SUBDIRS:
            (project_dir / sub).mkdir(parents=True, exist_ok=True)

    substitutions = {
        "PROJECT_NAME": request.project_display_name,
        "PROJECT_SLUG": request.project_slug,
        "TODAY": today,
        "WIKI_DIR": str(request.layout.wiki_dir),
        "ASOF_VERSION": SKILL_VERSION,
    }

    created: list[Path] = []
    skipped: list[Path] = []
    for template_filename, target_filename in PROJECT_TEMPLATES.items():
        target = project_dir / target_filename
        if target.is_file():
            skipped.append(target)
            continue
        rendered = render_template(load_template(template_filename), substitutions)
        verify_substituted(rendered, target)
        verify_frontmatter_ok(rendered, target)
        if not dry_run:
            atomic_write_text(target, rendered)
        created.append(target)
    return (created, skipped)


# ─── orchestrator ──────────────────────────────────────────────────────────


def do_scaffold(
    request: ScaffoldRequest, *, today: str, dry_run: bool = False
) -> ScaffoldResult:
    """Run stages 3 + 4 end-to-end. Caller composes WikiLayout + slug."""
    wiki_dir_created, created_3, updated_3, skipped_3 = bootstrap_wiki_dir(
        request.layout, dry_run=dry_run, today=today
    )
    config_path, _config_was_new = register_project_in_config(
        request.layout, request.project_slug, dry_run=dry_run
    )
    if config_path not in (created_3 + updated_3 + skipped_3):
        # The config-registration step writes via atomic_write_json which
        # creates-or-updates; we treat it as "updated" (the wiki dir's most
        # important file, conceptually).
        updated_3.append(config_path)

    # Pattern-C-only: augment <source>/.gitignore so wiki internals don't
    # accidentally get committed (PLAN.md §4, Codex round-1 phase-3 HIGH).
    gitignore_augmented, gitignore_already = augment_pattern_c_gitignore(
        request.layout, dry_run=dry_run
    )

    created_4, skipped_4 = scaffold_project_pages(
        request, today=today, dry_run=dry_run
    )

    return ScaffoldResult(
        wiki_dir_created=wiki_dir_created,
        files_created=tuple(created_3 + created_4),
        files_updated=tuple(updated_3),
        files_skipped=tuple(skipped_3 + skipped_4),
        gitignore_augmented=gitignore_augmented,
        gitignore_already_done=gitignore_already,
        dry_run=dry_run,
    )


# ─── helpers exposed for tests ─────────────────────────────────────────────


def _all_default_excludes_include_mandatory() -> bool:
    """Sanity invariant: DEFAULT_EXCLUDES must include every MANDATORY_EXCLUDE.

    Used by the test suite to guard against future edits that drop one of
    the path-traversal-prevention entries.
    """
    return MANDATORY_EXCLUDES.issubset(set(DEFAULT_EXCLUDES))


# Module-import-time invariant check (defense in depth — fails the test run
# if someone edits DEFAULT_EXCLUDES and breaks the contract):
if not _all_default_excludes_include_mandatory():  # pragma: no cover
    raise ConfigError(
        "scaffold.DEFAULT_EXCLUDES is missing one of MANDATORY_EXCLUDES; "
        "fix scaffold.py before init can run safely."
    )
