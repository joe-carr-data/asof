# asof ingest procedure

> The agent's step-by-step guide for transitioning a conformant wiki to a new conformant state after `asof:sync` has detected deltas. Loaded on demand from `skills/sync/SKILL.md` (kept separate to keep the skill's loaded context lean).

This document assumes you have already read [SCHEMA.md](SCHEMA.md). Procedures here apply the rules there.

---

## Table of contents

1. [Preconditions](#1-preconditions)
2. [Reading the delta report](#2-reading-the-delta-report)
3. [Procedure for NEW files](#3-procedure-for-new-files)
4. [Procedure for MODIFIED files](#4-procedure-for-modified-files)
5. [Procedure for DELETED summaries](#5-procedure-for-deleted-summaries)
6. [Procedure for skipped symlinks](#6-procedure-for-skipped-symlinks)
7. [Updating bookkeeping](#7-updating-bookkeeping)
8. [Lint pass](#8-lint-pass)
9. [Final report to the user](#9-final-report-to-the-user)
10. [Failure recovery](#10-failure-recovery)

---

## 1. Preconditions

Before starting an ingest cycle, verify:

- [ ] `asof:sync` ran successfully (exit 0). The delta report lives at `<wiki_dir>/.last-sync/<project>.json`.
- [ ] You are operating against the wiki dir reported in the sync output, not a different wiki.
- [ ] No other Claude Code session is mid-ingest. **Note:** `<wiki_dir>/.asof.lock` is created on first sync and persists between runs as a marker; its presence does *not* mean a run is currently active (the kernel-level `flock` is what determines that, and the file mtime is a weak hint at best). If you suspect a concurrent ingest, ask the user before proceeding.

If any precondition fails, report the issue to the user and stop.

---

## 2. Reading the delta report

Open `<wiki_dir>/.last-sync/<project>.json`. The shape is documented in [SCHEMA.md §11](SCHEMA.md#11-last-sync-delta-report-wiki_dirlast-syncprojectjson) (matches the JSON written by `skills/sync/scripts/report.py:serialize_to_dict`).

Quick triage:

```bash
# Total work to do
jq '.totals.total_changes' .last-sync/<project>.json

# Per-category counts
jq '.totals' .last-sync/<project>.json
```

If `totals.total_changes == 0`: the wiki is already current. Nothing for you to do. Append a single `## [<date>] sync | no deltas` line to `log.md` (the `sync` action is allowed in SCHEMA §8 specifically for this case) and return.

If `totals.total_changes > 10`: surface the count to the user before proceeding. They may want to scope down (`--summary-only` re-run, partial ingest, etc.). Don't silently process 50 files at once.

For 1–10 deltas: proceed to the per-category procedures below.

---

## 3. Procedure for NEW files

For each entry in `deltas.new`:

### 3.1 Capture the source's mtime first (SCHEMA Rule 1)

The mtime is in the delta record itself — read it from the JSON before opening the source:

```bash
# Capture mtime from the delta record (NOT from the file system again)
NEW_MTIME=$(jq -r ".deltas.new[] | select(.rel_path == \"<rel_path>\") | .mtime" .last-sync/<project>.json)
```

This implements SCHEMA Rule 1 — never invent or re-derive the mtime. The sync skill already captured it; reuse that.

### 3.2 Read the source

```bash
cat <wiki_dir>/raw/<project>/<rel_path>
```

You need the full content of the source — not a summary, not a fragment.

### 3.3 Decide page-type implications

A NEW source touches at minimum:

- A **new source-summary** at `wiki/<project>/sources/<mirror-of-source-rel-path>.md`.
- Zero or more **entity / concept page updates** if the source describes things you already track (or warrants new entity / concept pages).
- The **`current_state.md` page** if the source materially changes the project's overall state.
- The **`index.md`** entry for the new source-summary.
- The **`log.md`** ingest entry.
- Possibly **`_candidates.md`** if new candidate concepts appeared.

### 3.4 Write the source-summary

Create `wiki/<project>/sources/<mirror-path>.md` with:

```yaml
---
title: Source — <source-filename>
type: source-summary
project: <slug>
sources:
  - path: raw/<project>/<rel_path>
    source_mtime: <mtime from delta report>
    ingested: <today, YYYY-MM-DD>
tags: [<inferred from content>]
last_updated: <today, YYYY-MM-DD>
---

# <source-filename>

<one-paragraph "what this source is and why it matters">

## <semantic sections summarizing the source>

## Cross-references

- [entity_page](../entities/foo.md)
- [concept_page](../concepts/bar.md)
```

The path under `sources/` should mirror the source's path under `raw/<project>/`. Example:

| Source path | Source-summary path |
|---|---|
| `raw/foo/README.md` | `sources/README.md` |
| `raw/foo/docs/design.md` | `sources/docs/design.md` |
| `raw/foo/docs/archive/old.md` | `sources/docs/archive/old.md` |

### 3.5 Update / create entity and concept pages

For every entity or concept the source mentions:

- If a page already exists: read it; integrate the new claim per [SCHEMA.md §6](SCHEMA.md#6-time-aware-ingest-rules) (newer source supersedes older). Add a citation to the new source in the page's `sources:` frontmatter list. Bump `last_updated`.
- If no page exists and this concept is mentioned in **3+** sources (across history, not just this ingest): promote from `_candidates.md` to a new `concepts/<slug>.md` page.
- If no page exists and this is a first / second mention: add to `_candidates.md` with mention count.

### 3.6 Update `current_state.md`

After processing all entity / concept changes, re-read `current_state.md` and update any sections that the new source materially affects. Typical updates:

- Status flips (e.g. "X is in progress" → "X is shipped").
- New facts that supersede old ones (with proper supersession note).
- Counters / metrics that have moved.

`current_state.md` is the user's first read; treat it as the headline.

---

## 4. Procedure for MODIFIED files

For each entry in `deltas.modified`:

### 4.1 Read both versions

```bash
# The (now-current) raw version
cat <wiki_dir>/raw/<project>/<rel_path>

# The existing source-summary (it has the OLD source_mtime)
cat <wiki_dir>/wiki/<project>/sources/<mirror-of-rel-path>.md
```

### 4.2 Apply self-supersession

In the source-summary:

1. Update `source_mtime` in frontmatter from `old_mtime` to `new_mtime`.
2. Append `old_mtime` to a `previous_mtimes:` list (create it if missing).
3. Add a new `## Self-supersession (YYYY-MM-DD)` section to the body.
4. Bump `last_updated`.

```markdown
## Self-supersession (2026-04-26)

**Earlier version (mtime 2026-02-05)** said:
- X
- Y

**Current version (mtime 2026-04-26)** says:
- X' (previously X — the change is …)
- Z (new — Y is no longer claimed)

Implications: <what entity / concept pages should change>.
```

### 4.3 Re-evaluate cited pages

Find every entity / concept page that cites this source (grep their `sources:` frontmatter for the path). For each:

- If a claim there was based on the *old* content and the *new* content contradicts it, apply the standard supersession pattern (§6).
- If the claim is still consistent with the new version, no change.

### 4.4 Update `current_state.md` if needed

Same as §3.6 — flip statuses, refresh counters, record the supersession.

---

## 5. Procedure for DELETED summaries

For each entry in `deltas.deleted`:

### 5.1 Open the orphan summary

```bash
cat <summary_path>   # the path is in the delta record
```

### 5.2 Mark as removed-upstream

In the source-summary's frontmatter:

```yaml
removed_upstream: 2026-04-26
```

In the body, append a section:

```markdown
## Removed upstream (2026-04-26)

This source was deleted from the upstream repo on or before this date. The summary is preserved as historical record per the schema's persistence-over-deletion rule (SCHEMA.md §6, Rule 5).

Last known content: <one or two sentences capturing the gist, so future readers don't need to dig the file out of git>.
```

Bump `last_updated`.

### 5.3 Find orphan claims

Grep every entity / concept page for this source's `path:`. For each page that cites this now-removed source:

- If the page has at least one *other* source backing the same claim, no action needed (the claim is still backed).
- If this was the *only* source for some claim, mark it inline:

  ```markdown
  Foo is currently 42. <!-- backing source removed: 2026-04-26 -->
  ```

  Bump the page's `last_updated`. Surface these markers to the user in the final report (§9) — they decide whether the claim still holds.

---

## 6. Procedure for skipped symlinks

If `deltas.skipped_symlinks` is non-empty, the user has symlinks under `raw/` that were skipped by `--safe-links`.

Default action: **report and move on**. The symlinks point to other files (often in the same source); their targets either appear elsewhere in the delta report (and get processed normally) or were excluded by the rsync filters.

If the user explicitly wants symlinks resolved into the wiki, suggest re-running with `asof:sync --copy-links`. Don't silently follow them yourself.

---

## 7. Updating bookkeeping

After processing all deltas:

### 7.1 `index.md`

Add a one-line entry for every NEW source-summary created. Format:

```markdown
- [<source-rel-path>.md](sources/<mirror-path>.md) — <YYYY-MM-DD>, <one-line summary>
```

Group by category (Top-level, Entities, Concepts, Source summaries). Update the "Source summaries" section's grouping if the wiki uses date-based or topic-based subgroups.

### 7.2 `log.md`

One entry per delta processed. Use the format from [SCHEMA.md §8](SCHEMA.md#8-log-format):

```markdown
## [2026-04-26] ingest | raw/<project>/<rel> | mtime=<source_mtime>
- <one-line summary>
- pages touched: <comma-separated relative paths>
```

For self-supersession, removal, and lint cycles, use the appropriate action keyword (see §8 of SCHEMA.md).

### 7.3 `_candidates.md`

If new candidate concepts emerged:

- Increment the mention count for existing entries the source touched.
- Add new entries for first-mention candidates.
- Promote any candidate at 3+ mentions to a `concepts/<slug>.md` page (and remove it from `_candidates.md`). Append a `## [<date>] candidate-promoted | <name>` entry to `log.md`.

### 7.4 `current_state.md`

Recompute the headline synthesis if any delta materially affected it. Bump `last_updated`.

---

## 8. Lint pass

After all bookkeeping updates, run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/lint.py --project <name>
```

(or the equivalent skill invocation when phase 4 is shipped.)

For each lint finding:

- **Auto-fixed** (rule 1, rule 4 with `--fix`): no action needed; the fix is logged.
- **Mtime drift warning**: read the affected page, verify it still reflects current state. If yes, just bump `last_updated`. If no, do a mini-update.
- **Missing mtime / frontmatter validity errors**: a data-quality bug. Stop and surface to the user — don't try to invent a value.
- **Orphan pages**: add an `index.md` entry pointing to them.
- **Removed-source claims**: surface the `<!-- backing source removed -->` count to the user.

---

## 9. Final report to the user

End the ingest cycle with a structured summary. The shape:

```markdown
## Ingest summary

**Project:** <name>
**Deltas processed:** N NEW + M MODIFIED + K DELETED
**Pages created:** ...
**Pages updated:** ...
**Self-supersessions recorded:** N
**Orphan claims flagged:** K (from the K removed-upstream sources)
**Candidates promoted:** ... (or "none")
**Lint findings:** ... (or "clean")

**For your attention:**
- <any orphan claims that need a human decision>
- <any contradictions you couldn't resolve automatically>
- <any newly-promoted concepts the user should review>
```

Don't end the cycle without surfacing things that need a decision. The wiki can integrate machine-resolvable changes silently; everything else needs human input.

---

## 10. Failure recovery

| Symptom | Cause | Recovery |
|---|---|---|
| Sync exited non-zero | rsync failed (permission, missing source, etc.) | Don't ingest. Report the error. Re-run sync after fixing. |
| `<wiki_dir>/.last-sync/<project>.json` missing | Sync didn't complete | Re-run `asof:sync` first. |
| Compat-matrix cell (b) — read-only | Skill is too old to write | Stop. Tell user to upgrade asof to `min_writer_version`. |
| Cell (c) — `--migrate` required | Wiki schema older than skill schema | v1: refuse with clear message. v1.x: run migration script first. |
| Mid-ingest, lint fails on a page you just wrote | You produced invalid frontmatter | Re-read the page, fix the frontmatter, re-run lint. Don't silently swallow lint errors. |
| Mid-ingest, two source-summaries claim the same path | Bug — should be impossible after slugification | Surface to user; don't auto-merge. |
| User aborts mid-ingest | Ctrl-C during your work | Append a `## [<date>] ingest-aborted | partial` line to `log.md`; future runs detect the partial state and re-ingest. |

The cardinal rule: **never lose information**. The wiki preserves history through supersession, removal markers, and the log. If a procedure step fails, append a log entry and stop — partial progress is better than silent corruption.
