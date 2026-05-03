# asof schema specification

> The contract that defines what a valid `asof` wiki looks like. The skills (`init`, `sync`, `lint`) are mechanisms for keeping a wiki conformant to this spec; the agent ingests deltas by following [INGEST_PROCEDURE.md](INGEST_PROCEDURE.md), which transitions a conformant wiki to a new conformant state.

**Schema version:** `1.0`

This is the v1.0 schema. Changes to anything in this document follow the discipline in [PLAN.md](../PLAN.md) section 17: additive-only between minor versions; breaking changes only on major bumps with migration scripts. CI lints every shipped example wiki against this spec on every PR.

---

## Table of contents

1. [Overview](#1-overview)
2. [Folder layout](#2-folder-layout)
3. [Required frontmatter](#3-required-frontmatter)
4. [Page types](#4-page-types)
5. [Source-summary structure](#5-source-summary-structure)
6. [Time-aware ingest rules](#6-time-aware-ingest-rules)
7. [Bookkeeping files](#7-bookkeeping-files)
8. [Log format](#8-log-format)
9. [Candidate promotion](#9-candidate-promotion)
10. [Lint rules](#10-lint-rules)
11. [Last-sync delta report (`.last-sync/<project>.json`)](#11-last-sync-delta-report-wiki_dirlast-syncprojectjson)
12. [Schema-version compatibility matrix](#12-schema-version-compatibility-matrix)

---

## 1. Overview

An `asof` wiki is a directory of plain Markdown files that an LLM agent maintains in response to changes in a *source* corpus (a code repo, a research collection, etc.). Every claim in the wiki carries a timestamp (the `source_mtime` of the document it cites), and the wiki applies **supersession rules** so that newer claims systematically replace older ones — without losing the history.

Two layers:

- `raw/` — an rsync-managed mirror of `*.md` files from the source. **Agent-read-only.**
- `wiki/` — pages the LLM owns: source-summaries, entity pages, concept pages, current-state synthesis, bookkeeping. **Agent-owned, must conform to this spec.**

The skill never modifies `raw/` (only `rsync` does). The agent never modifies `raw/` (it's read-only from the agent's perspective). The agent owns `wiki/` end-to-end.

---

## 2. Folder layout

```
<wiki_dir>/
├── .asof.json              # configuration (see PLAN.md section 5)
├── .asof.lock              # transient: file lock during sync/lint
├── .last-sync/             # one JSON delta report per project
│   └── <project>.json
├── .pending-sync/          # transient: per-project debounce stamps for the hook
│   └── <project>.stamp
├── CLAUDE.md               # written by `asof:init` from templates/wiki_root_CLAUDE.md
├── raw/                    # rsync-managed mirror; agent-read-only
│   └── <project>/...
└── wiki/                   # agent-owned
    └── <project>/
        ├── current_state.md
        ├── index.md
        ├── log.md
        ├── _candidates.md
        ├── entities/
        │   └── <name>.md
        ├── concepts/
        │   └── <name>.md
        └── sources/
            └── <mirrors-source-tree>.md
```

**Pattern A** (shared wiki, multiple projects under home): `<wiki_dir>` is `~/.claude/asof/`; each project gets its own `raw/<project>/` and `wiki/<project>/`.

**Pattern B** (per-project under home): each project's wiki is its own `<wiki_dir>` (e.g. `~/.claude/asof-work/`).

**Pattern C** (in-repo): `<wiki_dir>` is `<repo-root>/.asof/`. `wiki_dir` and `source` are omitted from `.asof.json` and auto-derived (Codex round-2 fix).

### Folder invariants (enforced by sync/lint)

- `raw/` is **a mirror, not immutable**. `rsync --delete` removes upstream-deleted files. The wiki preserves history of deleted sources via `removed_upstream:` markers in source-summaries (see §5).
- All paths in `<wiki_dir>/raw/<project>/` and `<wiki_dir>/wiki/<project>/` must resolve **inside** `<wiki_dir>` after `Path.resolve()` (path-traversal guard).
- `.asof` and `.last-sync` are **mandatory** entries in every project's `excludes` list to prevent Pattern C self-ingest.

---

## 3. Required frontmatter

Every wiki page (anything under `wiki/<project>/`) must begin with YAML frontmatter fenced by `---`:

```yaml
---
title: <human-readable page title>
type: source-summary | entity | concept | comparison | overview | synthesis
project: <slug, matches one of .asof.json's projects[].name>
sources:
  - path: raw/<project>/<rel-path>.md
    source_mtime: 2026-04-26      # YYYY-MM-DD, the cited file's mtime
    ingested: 2026-04-26          # YYYY-MM-DD, when the agent ingested it
tags: [domain, topic, ...]        # optional but recommended
last_updated: 2026-04-26          # YYYY-MM-DD, when this wiki page was last touched
---
```

### Required fields

| Field | Type | Description |
|---|---|---|
| `title` | string, non-empty | What this page is about. Free-form. |
| `type` | enum (see §4) | Page type. Drives expectations about content + lint rules. |
| `project` | string, slug | Project this page belongs to. Must match a configured project name. |
| `last_updated` | ISO date | When the agent last touched this page. Bumped on every edit. |

### Required for `source-summary` pages

| Field | Type | Description |
|---|---|---|
| `sources` | list of objects | Cited raw documents. **Source-summary pages must cite exactly one source** (the document the summary describes). Other page types may cite many. |
| `sources[].path` | string | Relative path under `raw/`. Quoted forms (single or double quotes) are accepted to support paths with spaces. |
| `sources[].source_mtime` | ISO date | Cited file's mtime. The most important field in the schema. |
| `sources[].ingested` | ISO date | When this citation was first written. |

### Optional fields

| Field | Type | Description |
|---|---|---|
| `tags` | list of strings | Free-form tags. Used for cross-referencing and Obsidian queries. |
| `previous_mtimes` | list of ISO dates | Self-supersession history (see §6). Each entry is the `source_mtime` from a prior version of the cited source. |
| `removed_upstream` | ISO date | Set on source-summaries whose `path` no longer exists in `raw/`. The summary is preserved as historical record (see §6). |
| `mtime_corrected` | ISO date | Bookkeeping marker: indicates the `source_mtime` was corrected after the original ingest (manual fix). |
| `aliases` | list of strings | Alternative names this page can be referenced by (entity / concept pages). |

---

## 4. Page types

| Type | Purpose | Required fields beyond §3 |
|---|---|---|
| `source-summary` | One page per ingested source. Summarizes the document; tracks `source_mtime`. | `sources` (exactly 1 entry) |
| `entity` | Things that exist in the world the wiki describes — people, systems, projects, products. | — |
| `concept` | Ideas, rules, contracts, methodologies. | — |
| `comparison` | Side-by-side evaluation of N alternatives. | — |
| `overview` | Top-level synthesis (e.g. `current_state.md`). | — |
| `synthesis` | Cross-cutting narratives that pull from many sources. | — |

Each type has a conventional location:

- `source-summary` → `wiki/<project>/sources/<mirror-of-source-path>.md`
- `entity` → `wiki/<project>/entities/<slug>.md`
- `concept` → `wiki/<project>/concepts/<slug>.md`
- `comparison`, `overview`, `synthesis` → `wiki/<project>/<topic>.md` (or sub-dirs at the agent's discretion)

**`current_state.md`** is a special `overview` page at the top of every project: it always reflects the latest cumulative synthesis. The agent updates it on every ingest.

---

## 5. Source-summary structure

A source-summary is the canonical record of one ingested source. Its body follows this loose convention:

```markdown
---
title: Source — <source-filename>
type: source-summary
project: <slug>
sources:
  - path: raw/<project>/<rel>.md
    source_mtime: 2026-04-26
    ingested: 2026-04-26
tags: [domain, topic]
last_updated: 2026-04-26
---

# <source-filename> (<full path>)

<one-paragraph description of what the source is and why it matters>

## <semantic section>
...

## <semantic section>
...

## Cross-references
- [entity page](../entities/foo.md)
- [concept page](../concepts/bar.md)
```

### Self-supersession (when a source is re-ingested with newer mtime)

If the same source path is re-ingested with a newer `source_mtime`, the agent updates the source-summary in place:

1. Update `source_mtime` in frontmatter to the new value.
2. Append the old mtime to a `previous_mtimes:` list in frontmatter.
3. Add a `## Self-supersession (YYYY-MM-DD)` section to the body explaining what changed:

```markdown
## Self-supersession (2026-04-26)

**Earlier version (mtime 2026-02-05)** said X.

**Current version (mtime 2026-04-26)** says Y.

Implications: Z.
```

The agent must also revisit every entity / concept page that cites this source and apply the standard supersession pattern (§6) wherever the new content contradicts an older claim.

### Removal (when the source disappears upstream)

When `rsync --delete` removes a file that was previously ingested, the source-summary is **not deleted**. Instead:

1. Add `removed_upstream: <today>` to frontmatter.
2. Add a `## Removed upstream (YYYY-MM-DD)` section to the body explaining when and (if known) why.
3. Find every entity / concept page that cited this source. If a claim's only backing was this now-removed source AND no newer source supersedes it, flag it inline:

   ```markdown
   <!-- backing source removed: 2026-04-26 -->
   ```

   These markers surface in `asof:lint` output for the user to triage.

---

## 6. Time-aware ingest rules

These are the headline rules. Apply them on every ingest.

### Rule 1 — Read mtime first

Before reading any source's content, record its `source_mtime` (via `stat`) in the source-summary's frontmatter. The mtime is the most important field in the schema — it is how the wiki distinguishes stale claims from current ones.

### Rule 2 — Newer source wins (cross-source supersession)

When a new source contradicts a claim already in the wiki (originating from a different source):

- If the new source's `source_mtime` is **more recent**, replace the old claim. Keep a supersession note in the affected entity / concept page:

  ```markdown
  Foo is currently 42.
  
  > **Previously:** Foo was 41 (per `OLD_DOC.md`, 2026-01-05) — superseded by `NEW_DOC.md` (2026-04-26).
  ```

- If the new source is **older** than what's already in the wiki, do NOT overwrite. Add it as historical context only (e.g. an "Earlier framing" section).

- If the two sources are **concurrent** (within 7 days), surface the conflict explicitly and let the user resolve it before integrating.

### Rule 3 — Maintain a "current state" page

Every project has a `current_state.md` that reflects only the latest synthesis. Historical claims contribute via supersession notes, never by polluting the current state.

### Rule 4 — Self-supersession (same source, newer version)

Covered in §5. The mechanism is distinct from cross-source supersession: it happens *within* a source-summary, recording the document's evolution, not a contradiction between documents.

### Rule 5 — Persistence over deletion

Source-summaries for upstream-deleted files are preserved with `removed_upstream:`. The wiki's job is to remember, not to mirror; deletion in `raw/` is a mtime event, not a "forget about this" signal.

---

## 7. Bookkeeping files

Every project has four required bookkeeping files at `wiki/<project>/`:

### `index.md`

Content catalog. One line per page, grouped by category. The agent updates it on every ingest. Used by future skills (and humans) to navigate the wiki without traversing the full tree.

```markdown
# <project> wiki — index

## Top-level
- [current_state.md](current_state.md) — latest synthesis
- [_candidates.md](_candidates.md) — deferred concept candidates
- [log.md](log.md) — chronological audit trail

## Entities
- [foo.md](entities/foo.md) — what foo is
- [bar.md](entities/bar.md) — what bar is

## Concepts
- [some-rule.md](concepts/some-rule.md) — the some-rule rule

## Source summaries
- [foo-summary.md](sources/foo-summary.md) — 2026-04-26
- [bar-summary.md](sources/bar-summary.md) — 2026-04-25
```

### `log.md`

Append-only chronological record of every ingest, query, and lint pass. See §8.

### `_candidates.md`

Deferred concept candidates: ideas mentioned <3 times across sources. See §9.

### `current_state.md`

The headline synthesis, one per project. Always reflects the latest claims (see Rule 3). On every ingest, the agent re-evaluates and updates this file.

---

## 8. Log format

`log.md` entries use a greppable prefix (`grep "^## \[" log.md`):

```markdown
## [2026-04-26] ingest | raw/<project>/docs/foo.md | mtime=2026-04-26
- one-line summary of what changed
- pages touched: sources/foo-summary.md, entities/foo.md, current_state.md
- candidates updated: bar (count 2 → 3, promoted to concepts/bar.md)
```

### Allowed actions

| Action | When to log | Required body fields |
|---|---|---|
| `scaffold` | When a wiki dir or project subtree is first bootstrapped by `asof:init` | brief description of what was created |
| `ingest` | After a NEW or MODIFIED source is integrated | `pages touched`, optional `candidates updated` / `supersedes` |
| `sync` | After every `asof:sync` invocation, even when no deltas were detected | `deltas: NEW=N MODIFIED=M DELETED=K` (zero counts allowed) |
| `self-supersession` | When a source-summary gets a `previous_mtimes` bump | `pages touched` (cite all entity / concept pages re-evaluated) |
| `removed-upstream` | When a source disappears and is marked `removed_upstream` | `pages touched`, `orphan claims` (count of `<!-- backing source removed -->` markers added) |
| `candidate-promoted` | When a `_candidates.md` entry hits the promotion threshold and is moved to `concepts/` | `from: <count>` (mentions when promoted), `to: concepts/<slug>.md` |
| `query` | After answering a non-trivial user question (optional but useful) | one-line answer summary |
| `lint` | After every `asof:lint` invocation | `findings: <count>`, `auto-fixed: <count>` |
| `mtime-correction` | Bookkeeping fix (see §3 `mtime_corrected`) | the corrected source path + old/new mtime |
| `tooling-fix` | Skill / config / process changes | what was fixed |
| `ingest-aborted` | Mid-ingest abort (Ctrl-C, error) so the next run knows the partial state | which delta types were processed before the abort |

---

## 9. Candidate promotion

`_candidates.md` is a list of half-formed concepts the wiki has noticed but not yet promoted to first-class concept pages.

**Mention threshold: 3.** When a candidate has been referenced by 3 or more sources, the agent promotes it to `concepts/<slug>.md`.

Format:

```markdown
# <project> — candidate concepts

## <candidate-name>
- mention count: 2
- first seen: 2026-03-15 (in `sources/foo.md`)
- most recent mention: 2026-04-20 (in `sources/bar.md`)
- summary: <one-line description>
```

When promoted, the candidate is removed from `_candidates.md` and a new `concepts/<slug>.md` page is created with backlinks to the sources that mentioned it.

The threshold is configurable per wiki via `lint_thresholds.candidate_promotion_threshold` in `.asof.json` (default 3) — though only the lint rule reads it; the agent is encouraged to use judgment.

---

## 10. Lint rules

`asof:lint` (phase 4) checks every wiki page against these rules. Each has a default threshold; thresholds are configurable per wiki via `lint_thresholds` in `.asof.json`.

| # | Rule | Default threshold | Severity |
|---|---|---|---|
| 1 | **Mtime drift** — `last_updated` more than N days older than newest cited `source_mtime` | 30 days | warning |
| 2 | **Supersession gap** — page cites two sources whose `source_mtime` differ by N+ days with no supersession note in body | 60 days | warning |
| 3 | **Missing mtime** — source-summary frontmatter without `source_mtime` | n/a | error (data quality) |
| 4 | **Orphan pages** — pages with no inbound link from `index.md` or any other wiki page | n/a | warning |
| 5 | **Removed-source claims** — pages with `<!-- backing source removed -->` markers | n/a | info (surface for triage) |
| 6 | **Path mismatch** — source-summary's `path:` does not exist under `raw/` AND `removed_upstream:` is not set | n/a | error (data quality) |
| 7 | **Frontmatter validity** — missing required fields (`title`, `type`, `last_updated`) | n/a | error (schema violation) |

`--fix` rewrites the safe ones (rule 1 by bumping `last_updated`; rule 4 by adding missing `index.md` entries). Never touches page content.

In **read-only mode** (compat-matrix cell b), `asof:lint --fix` is rejected — lint runs report-only.

---

## 11. Last-sync delta report (`<wiki_dir>/.last-sync/<project>.json`)

`asof:sync` writes a per-project JSON report to `<wiki_dir>/.last-sync/<project>.json` after every successful sync (skipped in `--dry-run` mode). This is the canonical machine-readable record of the most recent delta detection — what `asof:lint` and the agent's ingest procedure consume.

Top-level shape:

```json
{
  "schema_version": "1.0",
  "asof_version": "0.1.0-dev",
  "project_name": "<slug>",
  "raw_subdir": "raw/<project>",
  "wiki_subdir": "wiki/<project>",
  "rsync": {
    "succeeded": true,
    "return_code": 0,
    "transferred": 5,
    "deleted": 2,
    "dry_run": false
  },
  "deltas": {
    "new": [{"rel_path": "...", "mtime": "YYYY-MM-DD"}],
    "modified": [{"rel_path": "...", "old_mtime": "YYYY-MM-DD", "new_mtime": "YYYY-MM-DD"}],
    "deleted": [{"raw_path": "raw/<project>/...", "summary_path": "/abs/path/sources/...md"}],
    "skipped_symlinks": [{"rel_path": "...", "target": "..."}]
  },
  "totals": {
    "new": N,
    "modified": M,
    "deleted": K,
    "skipped_symlinks": S,
    "total_changes": N + M + K
  },
  "timestamp": "YYYY-MM-DDTHH:MM:SS+00:00"
}
```

Notes:
- `rsync` may be `null` when sync didn't run rsync (e.g. dry-run paths).
- `total_changes` deliberately excludes `skipped_symlinks` — symlinks are informational, not changes the agent must ingest.
- Atomic writes via temp-then-rename so a kill mid-write leaves either the previous file or the new one, never a partial.
- One file per project (path-traversal-safe via project slugification at sync time).

The producer is `skills/sync/scripts/report.py:serialize_to_dict`. The consumers are the agent's ingest procedure (see [INGEST_PROCEDURE.md §2](INGEST_PROCEDURE.md#2-reading-the-delta-report)) and `asof:lint` (phase 4).

## 12. Schema-version compatibility matrix

The full four-cell matrix from PLAN.md section 2.

| Skill version vs. wiki | Behavior |
|---|---|
| `skill_version < min_reader_version` | **REFUSE** with "upgrade asof to ≥ `<min_reader>`". |
| `min_reader_version ≤ skill_version < min_writer_version` | **READ-ONLY**: `asof:lint` runs (without `--fix`); `asof:sync`, `asof:init`, migrations are blocked. No writes to `wiki/`, `raw/`, or `.asof.json`. |
| `skill_version ≥ min_writer_version` AND `wiki_schema_version < skill_schema_version` | **REQUIRE `--migrate`**: refuse without the flag; never auto-upgrade silently. |
| `skill_version ≥ min_writer_version` AND `wiki_schema_version ≥ skill_schema_version` (newer skill, older or same wiki schema, no upgrade needed) | **PROCEED**: work normally. |

Migrations always preceded by an automatic `wiki/` → `wiki.bak.<timestamp>/` backup. Rollback is `mv wiki/ wiki.broken && mv wiki.bak.<timestamp>/ wiki/`.

The current skill writes:

- `schema_version: "1.0"`
- `min_reader_version: "1.0"`
- `min_writer_version: "1.0"`

into `.asof.json` on `asof:init`. These three numbers travel with the wiki. They are independent of the skill's own `version` field in `.claude-plugin/plugin.json`.

---

## See also

- [INGEST_PROCEDURE.md](INGEST_PROCEDURE.md) — the agent's step-by-step procedure for transitioning a conformant wiki to a new conformant state when deltas arrive.
- [KARPATHY_PATTERN.md](KARPATHY_PATTERN.md) — the original idea this schema is built on, with attribution.
- [PLAN.md](../PLAN.md) — the design doc this schema is the contract for.
- [CHANGELOG.md](../CHANGELOG.md) — schema-version evolution history (v1.0 onward).
