---
name: sync
description: Sync source repo .md files into the wiki's raw/ dir and detect what changed (NEW / MODIFIED / DELETED). Use when the user says "sync the wiki", "asof sync", "update the wiki", "ingest new docs", "refresh the wiki from <project>", "pull updates into the brain", or when source markdown has changed and the wiki may be stale.
when_to_use: Trigger phrases — "sync the wiki", "asof sync", "update the wiki", "refresh the wiki", "ingest new docs", "bring the wiki up to date", "pull updates from <project> into the brain", "what changed in the docs". Also fire when a recent .md edit suggests the wiki is now out-of-date.
allowed-tools: Bash(rsync *) Bash(stat *) Bash(find *) Bash(python3 *) Bash(grep *) Bash(diff *) Bash(ls *) Bash(cat *) Bash(jq *) Bash(test *) Read Write Edit AskUserQuestion
argument-hint: "[project-name (optional)] [--all] [--dry-run] [--summary-only] [--strict-mtime] [--non-interactive] [--auto-select-longest] [--copy-links] [--allow-self]"
---

# asof:sync

Mirror source repo `*.md` files into the wiki's `raw/` directory and detect deltas (NEW / MODIFIED / DELETED) for the agent to re-ingest into the wiki.

This skill **does not modify the wiki itself** — only the `raw/` mirror and per-project `.last-sync/<project>.json` reports. Wiki updates are the agent's job, following the procedure in [references/INGEST_PROCEDURE.md](../../references/INGEST_PROCEDURE.md) (phase 2).

## How to invoke

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py $ARGUMENTS
```

`$ARGUMENTS` may be empty (cwd-aware project auto-select), a project name, or any of the documented flags. The script handles wiki-dir resolution, version-compat checks, project selection, file locking, rsync invocation, delta detection, and report rendering.

### After sync reports deltas: the ingest UX

This skill prints a NEW / MODIFIED / DELETED report — but **the agent owns the wiki ingest**, not the script. After sync exits, you (the agent) walk the deltas and write source-summaries, update bookkeeping pages, etc. The UX for this varies wildly with delta count, so:

**Do NOT free-form-ask the user via prose** ("Want me to ingest now?"). Use Claude Code's `AskUserQuestion` tool to present a structured, click-to-select interface. Match the question to the delta scale:

- **0 deltas** — nothing to do; print "wiki is up to date" and exit.
- **1 delta** — just ingest it (single file, no confirmation needed).
- **2–5 deltas** — one AskUserQuestion: `Process all N now (Recommended)` / `Preview diff first, then I'll confirm` / `Skip ingest — I'll handle it later`.
- **6–20 deltas** — one AskUserQuestion: `Process all N now (Recommended)` / `Pause every 10 files for confirmation` / `Preview the first 3, then continue` / `Skip ingest`.
- **20+ deltas** — one AskUserQuestion: `Process all N now` / `Pause every 25 files (Recommended)` / `Batch by source subdirectory` / `One at a time — I want to read each` / `Skip ingest`.

**Why structured matters:** for large ingests the user is delegating a long autonomous run. They need to see "Recommended" tags up front, not scroll through prose to figure out what you're proposing. Structured questions also let them click "Recommended" in one keystroke instead of typing `yes` 50 times.

**During the ingest itself:** if you opted into "Pause every N", actually pause — invoke `AskUserQuestion` again at each checkpoint (`Continue with the next batch (Recommended)` / `Stop here, I'll review what's done so far` / `Switch to one-at-a-time mode`). Don't pretend to pause and then keep going — that defeats the user's choice.

**For DELETED deltas specifically:** these are SCHEMA §6.5 "removed_upstream" cases. Mark the existing source-summary with `removed_upstream: <today>` rather than deleting the page. One AskUserQuestion if there are 3+ deletions: `Mark all N as removed_upstream (Recommended)` / `Review each one — some may have been moved, not deleted` / `Skip`.

## What the script does, in order

1. **Resolve wiki dir** (4-step chain, first match wins):
   1. `--wiki-dir <path>` flag.
   2. `ASOF_DIR` env var.
   3. Walk up from `pwd` looking for `.asof/.asof.json` (Pattern C) or bare `.asof.json`.
   4. `~/.claude/asof/` default (Pattern A).
2. **Load + validate `<wiki_dir>/.asof.json`.** Mandatory excludes (`.asof`, `.last-sync`) enforced; project names slugified; subdirs containment-checked.
3. **Apply schema-version compat matrix** (PLAN section 2 — full four cells):
   - Skill < `min_reader_version` → REFUSE (exit 3).
   - `min_reader` ≤ skill < `min_writer` → READ_ONLY (sync rejected, lint OK in `asof:lint`).
   - skill ≥ `min_writer` AND wiki schema older than skill schema → require `--migrate` (which v1 refuses with a clear "not yet implemented" message).
   - skill ≥ `min_writer` AND schemas compatible → proceed.
4. **Resolve target project(s)**:
   - `--all` → every configured project.
   - `--project NAME` → that exact project, slugified for lookup.
   - Otherwise: cwd-aware auto-select. Single match auto-selects; multi-match prompts in interactive mode, fail-fast in non-interactive mode unless `--auto-select-longest` opts into the deepest-source heuristic.
5. **Acquire file lock** at `<wiki_dir>/.asof.lock` (`fcntl.flock`). Concurrent syncs queue cleanly; the change-reminder hook detects the lock and degrades to a passive notice.
6. **For each selected project**:
   - **Self-ingest guard**: refuse if `wiki_dir` is inside `source` and `.asof` is missing from excludes (Pattern C safety; `--allow-self` bypasses).
   - **Run rsync** with `-av --delete --prune-empty-dirs --safe-links` and per-project excludes. `--copy-links` swaps to symlink-following. `--dry-run` skips writes.
   - **Marker-only `CLAUDE.md` auto-exclusion**: if the source-root `CLAUDE.md` contains only `asof:init`'s marker-fenced `@`-import block (no user content), rsync excludes it from mirroring. Mixed `CLAUDE.md` (user guidelines + import block) syncs normally — the user's content is real source. This prevents asof's own bootstrap snippet from being ingested as if it were project source. The check is anchored to source-root `CLAUDE.md`; nested `CLAUDE.md` files (e.g. in subprojects) sync without inspection.
   - **Detect deltas**: walk `raw/<project>/` and compare each `.md`'s mtime to the recorded `source_mtime` in `<wiki>/sources/`. Emit NEW / MODIFIED / DELETED records. `--strict-mtime` raises on regressions (recorded mtime newer than file mtime).
   - **Write `<wiki_dir>/.last-sync/<project>.json`** atomically (per-project — no clobber across projects in shared wikis).
   - **Print human report** to stdout with NEW / MODIFIED / DELETED lists (`--summary-only` collapses to counts).
7. **Print run summary** with cross-project totals, or "wiki is up to date." if nothing changed.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (or no deltas) |
| 1 | rsync failure (one or more projects); other projects may have succeeded |
| 2 | Config / data-shape error |
| 3 | Version-compat refusal (skill too old, read-only, or schema-mismatch without `--migrate`) |
| 4 | Project-selection failure (unknown name, cwd no match in non-interactive mode, etc.) |
| 5 | User abort (interactive prompt declined) |

## Common invocations

```bash
# Sync the project that contains pwd (most common during a coding session)
python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py

# Sync a specific project by name
python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py traddea

# Sync everything in the configured wiki
python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py --all

# Preview what would change without writing
python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py --all --dry-run

# CI / scripted: no prompts, fail-fast on ambiguity
python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py --all --non-interactive --summary-only

# Strict bookkeeping check (catch wiki-side mtime regressions)
python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py --all --strict-mtime
```

## After the script runs

The script's job is finished when delta detection + the JSON last-sync report are written. **The wiki itself is not yet updated.**

The agent then takes over:
- For each NEW raw file: read it, write a source-summary in `<wiki>/sources/`, update relevant entity / concept pages, append a log entry, update `index.md`.
- For each MODIFIED file: open the existing source-summary, record the self-supersession (old `source_mtime` → new), update any entity / concept claims that reference this source, bump `last_updated`.
- For each DELETED summary: do **not** delete it — mark with `removed_upstream: <today>` in frontmatter, surface any orphan claims (no other source backs them) for the user to triage.

Detailed step-by-step ingest rules live in `references/INGEST_PROCEDURE.md` (phase 2). For now, the human report printed by `sync.py` is enough to do the ingest manually.

## When the script reports a failure

| Symptom | Likely cause | Fix |
|---|---|---|
| `no asof config at <path>` | The wiki isn't bootstrapped | Run `/asof:init` first (phase 3) |
| `config error — missing mandatory entries [".asof", ".last-sync"]` | User edited `.asof.json` and dropped a guard | Restore the excludes; re-run |
| `Upgrade asof to continue` | The wiki was written by a newer version | Update the asof skill installation |
| `read-only mode — sync rejected` | The skill is in compat cell (b) | Update asof to ≥ `min_writer_version` shown in the message |
| `--migrate is not yet implemented` | Wiki schema is older than skill schema | v1 doesn't auto-migrate; manually edit schema fields or downgrade |
| `current directory <X> is not inside any configured project's source` | Ran from outside any source | Pass `--project` or `--all`, or `cd` into a source |
| `cwd matches multiple projects: ...` (non-interactive) | Nested-source ambiguity | Pass `--project NAME` or `--auto-select-longest` |
| `refusing to sync: wiki_dir is inside source and '.asof' is missing` | Pattern C self-ingest guard | Add `.asof` to the project's excludes (PLAN.md section 5) |
| `rsync exited <N>` | Underlying rsync error | Check stderr; usually a permission, missing-source, or disk-space issue |

## Behavioral guarantees

- **Atomic writes**: per-project `.last-sync/<project>.json` is written via temp-then-rename. Process kill mid-write leaves either the old file or the new file, never a corrupted partial.
- **No silent failures**: every error path emits a single-sentence message to stderr with the exit code documented above.
- **Idempotent**: re-running with no source changes is a no-op (rsync transfers nothing; deltas are empty; "wiki is up to date.").
- **Single-writer**: the file lock prevents two `asof:sync` runs from racing on the same wiki. The hook (phase 2) checks the lock and backs off.

## Cross-references

- [PLAN.md](../../PLAN.md) section 6.2 — full sync skill spec
- [PLAN.md](../../PLAN.md) section 17 — schema-evolution discipline
- [scripts/sync.py](scripts/sync.py) — entry point
- [scripts/config.py](scripts/config.py) — wiki / project data model + load
- [scripts/resolution.py](scripts/resolution.py) — wiki-dir / project / version-compat resolution
- [scripts/delta.py](scripts/delta.py) — frontmatter parsing + delta detection (rglob fix lives here)
- [scripts/rsync_runner.py](scripts/rsync_runner.py) — rsync wrapper with safety guards
- [scripts/report.py](scripts/report.py) — JSON + human report rendering
- [scripts/utils.py](scripts/utils.py) — shared utilities (slug, lock, atomic write, version)
