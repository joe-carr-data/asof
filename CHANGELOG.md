# Changelog

All notable changes to `asof` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with the additional schema-version semantics described below.

## Schema-version semantics

`asof` distinguishes two kinds of versions:

- The **skill version** lives in the plugin manifest at
  `.claude-plugin/plugin.json` (`version` field) and is what `git tag`s point
  at. It tracks the code.
- Three **schema-version fields** live in the wiki's own config at
  `<wiki_dir>/.asof.json`. They track the wiki *format*, not the code:
  - **`schema_version`** — the wiki-format version this wiki was written
    against.
  - **`min_reader_version`** — the lowest skill version that can *read* a wiki
    of this `schema_version` without error.
  - **`min_writer_version`** — the lowest skill version that can *write*
    (sync, init, migrate) a wiki of this `schema_version`.

The plugin manifest holds plugin metadata only (name, version, author, etc.).
Schema versioning is a runtime concern of the wiki the skill operates on.

The four-cell compatibility matrix governs every skill invocation
(see [PLAN.md](PLAN.md) section 2):

| Skill version vs. wiki | Behavior |
|---|---|
| `skill_version < min_reader_version` | **Refuse** with "upgrade asof" error. |
| `min_reader_version ≤ skill < min_writer_version` | **Read-only**: `lint` is report-only (`--fix` is rejected); `sync`, `init`, migrations blocked. |
| `skill ≥ min_writer_version` AND `wiki_schema < skill_schema` | **Require explicit `--migrate`**; never auto-upgrade silently. |
| `skill ≥ min_writer_version` AND `wiki_schema ≥ skill_schema` (newer skill, older wiki, no upgrade needed) | **Work normally**, no migration. |

Migrations are always preceded by an automatic `wiki/` → `wiki.bak.<timestamp>/`
backup. Rollback is `mv wiki/ wiki.broken && mv wiki.bak.<timestamp>/ wiki/`.

## Schema-evolution discipline

To keep the maintenance cost of three shipped example wikis bounded
(see [PLAN.md](PLAN.md) section 17):

1. **Additive-only between minor versions.** New optional frontmatter fields,
   new optional page types, new lint rules that are off-by-default. Existing
   wikis stay valid.
2. **Migration scripts ship in the same PR as any breaking change.** The
   script updates the three shipped examples in the same PR.
3. **CI lints all shipped examples on every PR.** Schema-drift bugs are
   caught at merge time.
4. **Pre-migration backup is mandatory** for users.
5. **`min_reader_version` enforces forward-incompat** with a clear "upgrade
   asof" error.
6. **CHANGELOG.md is the source of truth for migrations.** Every breaking
   change has a CHANGELOG entry that names the migration script and what
   it does.

## [Unreleased]

## [1.0.0] — 2026-05-04

First stable release. Three skills (`init`, `sync`, `lint`), a PostToolUse change-reminder hook template, three shipped example wikis with CI lint coverage, and 595 unit + integration tests.

### Added
- **`asof:sync` skill** — rsync-mirrors source `*.md` files into `<wiki_dir>/raw/<project>/` and detects deltas (NEW / MODIFIED / DELETED) by comparing source mtimes against recorded `source_mtime` values in wiki source-summaries. Per-project locking via `fcntl.flock` on `<wiki_dir>/.asof.lock`. cwd-aware project auto-resolve. Per-project last-sync JSON reports.
- **`asof:init` skill** — 5-stage interactive wizard: preflight (Python 3.9+, rsync, git, Obsidian), wiki layout choice (Pattern A / B / C), wiki-dir + `.asof.json` creation, project page scaffold, integrations (CLAUDE.md snippet, change-reminder hook, settings file edits, optional first sync). Atomic writes, idempotent re-runs, marker-fenced edits.
- **`asof:lint` skill** — 7 page-level checks (frontmatter validity, path-mismatch, missing-mtime, removed-source, mtime-drift, supersession-gap, orphan-page) with severity grouping (3 ERROR, 3 WARN, 1 INFO). Pre-flight config-validity gate via shared `load_wiki_config` (halts on invalid config rather than cascading findings). Two narrow auto-fixes via `--fix`: insert today's date when `last_updated` is missing entirely, and append orphan-page entries to `index.md`. Read-only mode rejection per compat-matrix cell b. Text + JSON output.
- **Schema spec** — [`references/SCHEMA.md`](references/SCHEMA.md) defines required frontmatter, page types, time-aware ingest rules (Newer source wins, Self-supersession, Removal), and the four-cell version-compat matrix.
- **Hook template** — `templates/hooks/wiki_change_reminder.py` is a PostToolUse hook that reminds the agent to re-sync after `*.md` edits. Per-project debounced via O_EXCL atomic claim. Path-traversal-safe.
- **Three example wikis** at [`examples/`](examples/): `codebase-wiki/` (tinyapp CLI demo), `research-wiki/` (Kaplan 2020 → Hoffmann 2022 scaling-law cross-source supersession), `book-wiki/` (Kahneman, *Thinking Fast and Slow*). All Pattern C, all lint clean from a fresh checkout.
- **CI lint coverage** — `.github/workflows/lint-examples.yml` runs `lint --severity warn` on each example on every push and PR, plus a non-gating INFO pass for triage visibility.
- **595 unit + integration tests** — every check has happy + failure paths; init/sync/lint exercised as real subprocesses; lock-contention tests with readiness handshake; cwd/env wiki-dir resolution coverage.

### Schema (initial)

- `schema_version`: `"1.0"`.
- `min_reader_version` and `min_writer_version` in fresh-init wikis: `"1.0.0"` (the released skill version).

### Distribution

- Plugin manifest at `.claude-plugin/plugin.json`.
- Stdlib-only runtime (no `pip install`, no `npm i`, no `uv sync`); requires Python 3.9+ and `rsync`.
- Install: `/plugin marketplace add joe-carr-data/asof` then `/plugin install asof@asof` in any Claude Code session.

## [0.1.0-dev] — pre-release

Initial skeleton. Not yet functional. See `PLAN.md` for the locked design.
