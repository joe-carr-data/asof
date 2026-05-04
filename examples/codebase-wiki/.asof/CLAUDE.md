# /Users/jcarr/repos/asof/examples/codebase-wiki/.asof — asof wiki

> A time-aware wiki maintained by the `asof` Claude Code plugin.
> Bootstrapped on **2026-05-04** by `asof:init` (asof v0.1.0-dev).

## How to use this wiki

When you (the agent) need to answer a question about a project tracked here:

1. **Read `wiki/<project>/current_state.md` first** — it always reflects the latest synthesis.
2. **Read `wiki/<project>/index.md`** — one-line catalog of every page in the project.
3. Drill into specific entity / concept pages from the index.
4. Only after the wiki: cross-check with raw sources or external memory if needed.

## The schema

This wiki conforms to the **asof v1.0 schema**. The full specification lives
inside the asof plugin at `<plugin>/references/SCHEMA.md`. The headline rules:

- **Every wiki page records `source_mtime`** for the documents it cites.
- **Newer sources supersede older ones**, with explicit `Previously X — superseded by Y` notes that preserve history rather than overwrite it.
- **Source-summaries persist after upstream deletion** via `removed_upstream:` markers — the wiki remembers, it doesn't mirror.
- **`raw/` is rsync-managed and agent-read-only.** The agent owns `wiki/`.
- **Three layout patterns** are supported (this wiki is configured per `.asof.json`):
  - **A**: shared wiki, multiple projects.
  - **B**: per-project wiki under home.
  - **C**: wiki inside the source repo.

## Time-aware ingest in one paragraph

When `asof:sync` reports new / modified / deleted sources, follow
`<plugin>/references/INGEST_PROCEDURE.md`. The cardinal rules:

- **Read `source_mtime` first.** Before reading any source's content, record its
  mtime in the source-summary's frontmatter.
- **Newer wins.** When a new source contradicts an existing claim, the more
  recent `source_mtime` decides. Never silently overwrite — record the
  supersession.
- **Maintain `current_state.md`.** This page reflects only the latest claims.
  Historical claims contribute via supersession notes.
- **Persist deletions.** Source-summaries for upstream-deleted files stay in
  the wiki tagged `removed_upstream:`. Orphan claims (the only-source-was-this
  case) get inline `<!-- backing source removed: <date> -->` markers for the
  user to triage.

## Schema-version compatibility

This wiki was written against schema v1.0 by asof v0.1.0-dev. The
config file `.asof.json` carries:

- `schema_version` — the wiki-format version this wiki was written against.
- `min_reader_version` — the lowest skill version that can read this wiki.
- `min_writer_version` — the lowest skill version that can write to this wiki.

If you (the agent / a future skill) encounter a wiki whose `min_reader_version`
exceeds your version, **refuse to operate** and tell the user to upgrade.

## Configured projects

See `.asof.json` for the full list. Each project lives at:

- `raw/<project>/` — rsync-managed mirror of source `*.md` files (agent-read-only)
- `wiki/<project>/` — agent-owned pages (entities, concepts, source-summaries, bookkeeping)

## Cross-references

- `<plugin>/references/SCHEMA.md` — the full schema specification.
- `<plugin>/references/INGEST_PROCEDURE.md` — the agent's step-by-step ingest procedure.
- `<plugin>/references/KARPATHY_PATTERN.md` — the original LLM-wiki idea this wiki is built on.
- `<plugin>/PLAN.md` — design decisions and rationale.
- `<plugin>/CHANGELOG.md` — schema-version evolution history.
