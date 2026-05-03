# Changelog

All notable changes to `asof` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with the additional schema-version semantics described below.

## Schema-version semantics

`asof` ships with three version numbers in every config and plugin manifest:

- **`schema_version`** — the wiki-format version this skill was built for.
- **`min_reader_version`** — the lowest skill version that can *read* a wiki of
  this `schema_version` without error.
- **`min_writer_version`** — the lowest skill version that can *write* (sync,
  init, migrate) a wiki of this `schema_version`.

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

### Added
- Repository scaffold: `LICENSE` (MIT), `.gitignore`, `PLAN.md` (locked design
  after four rounds of Codex review), directory layout, `README.md` skeleton.
- This `CHANGELOG.md` with explicit schema-version semantics.

### Pending (phases 1–8 per PLAN.md section 15)
- `asof:sync` skill (phase 1)
- Schema spec + templates (phase 2)
- `asof:init` interactive wizard (phase 3)
- `asof:lint` skill (phase 4)
- Comprehensive test suite (phase 5)
- Three example wikis with CI lint coverage (phase 6)
- README polish + asciinema demo + screenshots (phase 7)
- v1.0 release with end-to-end install verification on macOS + Linux (phase 8)

## [0.1.0-dev] — pre-release

Initial skeleton. Not yet functional. See `PLAN.md` for the locked design.
