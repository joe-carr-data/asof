# asof — implementation plan

> **AS OF semantics for your Claude Code wiki.** Every claim knows when it was made; newer claims supersede older ones; the wiki tells you what's true *as of now* without losing the history.

A Claude Code plugin that gives the agent a sense of time across documentation. Built on Karpathy's wiki pattern, extended with a time-aware schema (mtime tracking, supersession rules, current-state pages). Ports and generalizes a working implementation that has been driving a real ML-pipeline project for the past month.

> **Revision history.** Initial draft → Codex code review (continuation_id `ce204aea-24a9-45f0-9c23-79ddd29f7113`, NOGO with two critical issues) → this revision applies all C1/C2 critical fixes, all important fixes (sync auto-select, per-project last-sync, schema version compat, concurrency locks, input sanitization, non-interactive flags), the verified hook contract (PostToolUse + JSON `additionalContext` + debounce), and an explicit schema-evolution discipline section so the three shipped examples are not load-bearing on every release.

## 1. Goals and non-goals

### Goals
- Anyone can install this and start maintaining a time-aware wiki for any project (codebase, research, book club, business KB — domain-agnostic).
- Production-ready: idempotent, defensive against the bugs we already hit (non-recursive `glob`, mtime regressions, false-positive deltas), tested helper scripts.
- Distributable as **one GitHub repo** installable as: a Claude Code plugin (preferred), a personal skill (`~/.claude/skills/`), or a project skill (`.claude/skills/`).
- Clean migration path for the existing user (`~/Desktop/Brain/` + `~/.claude/skills/brain-sync/`): old setup keeps working, opt-in upgrade.

### Non-goals
- No vector / embedding search. Karpathy's `index.md`-as-catalog approach scales to ~hundreds of pages.
- No GUI. Obsidian remains the recommended viewer.
- No auto cross-project ingest. Each project is registered explicitly.
- No "synthesize the wiki for me end-to-end" auto-mode. The agent is the synthesizer; we don't try to make the script do that.

## 2. Locked-in decisions

| Decision | Choice | Why |
|---|---|---|
| **Repo / plugin name** | `asof` | Borrows the temporal-database `AS OF` semantic — exact match for what we do. Free namespace on GitHub / PyPI / npm. |
| **Wiki dir patterns supported** | Three: (A) one shared dir, all projects; (B) one Obsidian vault per project; (C) wiki inside the repo at `<repo>/.asof/`. Default = A at `~/.claude/asof/`. | Different users / teams want different isolation. `init` asks; resolution order = `--wiki-dir` arg > `ASOF_DIR` env > walk-up from `pwd` looking for `.asof/` (Pattern C) > `~/.claude/asof/` default. **Hard guard**: refuse to sync if `wiki_dir` is *inside* `source` unless `--allow-self` is passed (prevents Pattern C self-ingest). |
| **Hook contract** | PostToolUse with **exit 0 + JSON `hookSpecificOutput.additionalContext`** (verified against official docs). 10,000-char cap per call. **Debounced via `<wiki_dir>/.pending-sync/<project>.stamp` per-project file** (suppress reminders within 30 s of last for *that* project; cross-project reminders are independent). | Exit-2-with-stderr (our initial design) is reserved for errors per the docs; using it for reminders abuses the semantics. JSON additionalContext appears next to the tool result and is saved in transcript — the documented mechanism for non-blocking context injection. Per-project debounce handles the "50-file MultiEdit fires 50 hooks in parallel" case without cross-project suppression. |
| **Schema version compatibility (full matrix)** | (a) `skill_version < min_reader_version` → **refuse** with "upgrade asof" error. (b) `min_reader_version ≤ skill_version < min_writer_version` → **read-only**: `lint` is report-only (`--fix` is rejected); `sync`, `init`, migrations blocked. Tool-internal writes (lockfiles, temp files) permitted; **no writes to `wiki/`, `raw/`, or `.asof.json`**. (c) `skill_version ≥ min_writer_version` AND `wiki_schema_version < skill_schema_version` → **require explicit `--migrate`**; never auto-upgrade silently. (d) `skill_version ≥ min_writer_version` AND `wiki_schema_version ≥ skill_schema_version` (newer skill reading older wiki, no upgrade needed) → **work normally, no migration**. Migrations always preceded by automatic `wiki/` → `wiki.bak.<timestamp>/` backup. | Bare `schema_version` isn't enough; users running an old skill against a newer wiki need a clear failure mode, not silent corruption. The four-cell matrix covers every direction (forward-incompat, partial-incompat, intentional upgrade, downgrade) without surprises. Backups make migrations reversible. Codex review rounds 2–3. |
| **Hook debounce (per-project)** | `<wiki_dir>/.pending-sync/<project>.stamp` — one stamp file per project, not a global one. 30 s suppression window. Resolved via `ASOF_PROJECT_NAME` env in the hook. | A global `.pending-sync` would let a write in project A suppress reminders for project B in shared wikis. Per-project keeps reminders accurate per-project. Codex review round 2. |
| **Distribution model** | Claude Code plugin bundling 3 skills + 1 hook template | Plugin is the doc-recommended way to ship multiple skills + hooks; skill descriptions stay focused. |
| **Skill split** | `init`, `sync`, `lint` (three skills inside the plugin) | Different lifecycles. `init` is one-time + must be deliberate (`disable-model-invocation: true`). `sync` is the high-frequency workflow. `lint` is read-only audit. |
| **Reading contract** | Lives in the project's CLAUDE.md as a templated snippet, not as a fourth skill | The wiki-precedence rule isn't a procedure — it's a fact about how the agent should read. CLAUDE.md is the right place. |
| **Schema spec** | Ships *inside* the plugin at `references/SCHEMA.md` | Single source of truth, versions with the plugin. `init` writes a stub `<wiki_dir>/CLAUDE.md` that points at the shipped spec, not a copy. |
| **Hook** | Opt-in template, not on by default | Hooks are surprising; users opt in during `init`. |
| **Helper language** | Python 3.9+ stdlib only | No `pip install` step required. Same constraint that made the current `brain-sync` work. |
| **Schema evolution discipline** | Additive-only between minor versions. Breaking changes only on major bumps with migration scripts that update shipped examples in the same PR. CI runs lint over every shipped example to catch breakage. | Codex review flagged that 3 examples × undisciplined evolution = 3× maintenance. With this discipline, the maintenance is roughly 1× regardless of count, and we get to keep the "domain-agnostic" proof that comes from shipping multiple examples. See section 17. |

## 3. Repository layout

```
asof/                                   # github.com/jcarr/asof (or org)
├── README.md                           # 30-second pitch + install + 5-min tutorial
├── LICENSE                             # MIT
├── CHANGELOG.md                        # versioned releases
├── PLAN.md                             # this file (during build)
├── .gitignore
├── plugin.json                         # plugin manifest
├── skills/
│   ├── init/
│   │   ├── SKILL.md                    # bootstrap a wiki for a project
│   │   └── scripts/init.py
│   ├── sync/
│   │   ├── SKILL.md                    # rsync sources + delta detect (current brain-sync, generalized)
│   │   └── scripts/sync.py             # the rglob-bug-fixed version
│   └── lint/
│       ├── SKILL.md                    # whole-wiki schema audit
│       └── scripts/lint.py             # 7 checks; --fix for safe ones
├── references/
│   ├── SCHEMA.md                       # canonical time-aware schema rules
│   ├── INGEST_PROCEDURE.md             # full ingest workflow (split out of SKILL.md)
│   └── KARPATHY_PATTERN.md             # original idea, attributed
├── templates/
│   ├── wiki_root_CLAUDE.md             # written into <wiki_dir>/CLAUDE.md by init
│   ├── wiki_index.md                   # initial index.md
│   ├── wiki_log.md                     # initial log.md
│   ├── wiki_candidates.md              # initial _candidates.md
│   ├── wiki_current_state.md           # initial current_state.md (placeholder)
│   ├── project_CLAUDE_snippet.md       # wiki-precedence section to append
│   └── hooks/
│       └── wiki_change_reminder.py     # generic PostToolUse hook
├── examples/
│   ├── codebase-wiki/                  # 3-5 .md sources + resulting wiki
│   ├── research-wiki/                  # research topic example
│   └── book-wiki/                      # fan-wiki style
├── scripts/
│   └── install.sh                      # convenience installer for non-plugin route
└── tests/
    ├── test_sync.py
    ├── test_lint.py
    ├── test_init.py
    └── fixtures/
```

## 4. Wiki dir patterns

Three layouts supported. `asof:init` walks the user through choosing one.

### Pattern A — shared wiki, multiple projects (default)

```
~/.claude/asof/
├── .asof.json                  # registry
├── CLAUDE.md                   # schema spec
├── raw/{traddea,notes,...}/
└── wiki/{traddea,notes,...}/
```

One vault, multi-project. Open in Obsidian to browse everything. Best for solo users with several related projects.

### Pattern B — one wiki per project, but still under home

Same shape as A, but each project gets its **own** `wiki_dir`. Different `~/.claude/asof-<name>/` (or any path the user picks). Used when projects are for different audiences (work vs. personal) or want separate Obsidian vaults but you don't want them inside the source repos.

### Pattern C — wiki inside the source repo

```
<repo-root>/
├── src/...
├── docs/...
├── .gitignore                   # auto-augmented to exclude raw/, .last-sync/
└── .asof/                       # the wiki, committed alongside code
    ├── .asof.json               # source path auto-derived as parent of .asof/
    ├── CLAUDE.md
    ├── raw/<single-project>/    # ← gitignored (it's a mirror, regenerable)
    ├── .last-sync/              # ← gitignored (per-run state)
    └── wiki/<single-project>/   # ← committed (the persistent artifact)
```

Wiki travels with the repo via git. Best for:
- Teams that want collaborators to get the wiki for free with a `git pull`
- Open-source projects that want the wiki as a public artifact
- Anyone who dislikes things in `~/.claude/`

**Pattern-C-specific behaviors** (locked in):

1. **`source` is auto-derived** as the parent of `.asof/`. The committed `.asof.json` does not store an absolute path. (Avoids the "fork the repo, path is wrong" problem.)
2. **`.gitignore` is auto-augmented** by `init`: appends `raw/` and `.last-sync/` (under the `.asof/` prefix). Existing `.gitignore` is preserved; never overwritten.
3. **Self-ingest hard guard**: `sync.py` refuses to run if `wiki_dir` is detected inside `source` (which it always is in Pattern C — the guard is satisfied because the *exclude list* removes `.asof/` from the rsync). If excludes are missing `.asof`, sync aborts with a clear error rather than recursing.
4. **Cross-project queries** don't work — each repo's wiki is independent. `.asof.json` only ever has one project entry.

### Resolution order (for sync / lint / hooks)

When a skill needs to find "the wiki for the current context", it checks in this order, first match wins:

1. Explicit `--wiki-dir <path>` flag.
2. `ASOF_DIR` env var (set by hook config or shell).
3. **Walk up from current working directory** looking for a `.asof/` dir or a `.asof.json` file. (Enables Pattern C — `cd` into a repo, sync just works.)
4. `~/.claude/asof/` default (Pattern A).

Step 3 is what makes Pattern C ergonomic — no env vars to set, no flags to pass; the skill finds the wiki by walking up from `pwd`.

**Project auto-selection (within a wiki) for sync/lint:**

After the wiki dir is resolved, if `cwd` is inside one of the configured projects' `source` directory, that project is auto-selected. Resolution rules:

- **Single match:** auto-select.
- **Multiple matches (nested sources)** in interactive mode: prompt the user.
- **Multiple matches in non-interactive mode** (`--non-interactive` / `ASOF_NON_INTERACTIVE=1` / CI): **fail-fast** with a clear error listing the matches and asking for `--project <name>`, `--all`, or `--auto-select-longest`.
- **`--auto-select-longest` flag:** opt-in deterministic heuristic. Picks the most specific (longest path) match. Logged in the report so the user knows what was chosen.
- **No match:** refuse with `"specify --project <name> or --all"`.

This avoids surprising "sync everything from any cwd" behavior in shared wikis (Codex review).

## 5. Configuration

### `<wiki_dir>/.asof.json` — the wiki's self-describing config

**Pattern A / B example** (the `wiki_dir` field is included because the config is not committed and the path is local-only):

```json
{
  "wiki_dir": "/Users/jcarr/.claude/asof",
  "schema_version": "1.0",
  "min_reader_version": "1.0",
  "min_writer_version": "1.0",
  "lint_thresholds": {
    "mtime_drift_days": 30,
    "supersession_gap_days": 60
  },
  "projects": [
    {
      "name": "traddea",
      "source": "/path/to/repo",
      "raw_subdir": "raw/traddea",
      "wiki_subdir": "wiki/traddea",
      "excludes": [
        "node_modules", ".git", "venv", ".venv", "dist", "build",
        "__pycache__", ".claude",
        ".asof", ".last-sync"
      ]
    }
  ]
}
```

**Pattern C example** (the config is committed inside the repo at `<repo>/.asof/.asof.json`; both `wiki_dir` and `source` are omitted so the config travels portably across forks/clones):

```json
{
  "schema_version": "1.0",
  "min_reader_version": "1.0",
  "min_writer_version": "1.0",
  "lint_thresholds": {
    "mtime_drift_days": 30,
    "supersession_gap_days": 60
  },
  "projects": [
    {
      "name": "myproject",
      "raw_subdir": "raw/myproject",
      "wiki_subdir": "wiki/myproject",
      "excludes": [
        "node_modules", ".git", "venv", ".venv", "dist", "build",
        "__pycache__", ".claude",
        ".asof", ".last-sync"
      ]
    }
  ]
}
```

**Field notes:**
- `wiki_dir` — **Pattern A/B only.** Omitted for Pattern C; the wiki dir is the directory containing `.asof.json`. Storing an absolute path in committed config would break on clone (Codex review round 2).
- `min_reader_version` / `min_writer_version` — skill compatibility floor. See section 2 for the four-cell compat matrix.
- `lint_thresholds` — optional per-wiki overrides for mtime-drift and supersession-gap rules (defaults shown).
- `excludes` — `.asof` and `.last-sync` are always present in defaults (prevents Pattern C self-ingest). Missing them aborts sync with a clear error.
- `source` — for Pattern C, **omit this field**: auto-derived as the parent of `.asof/`. For Pattern A/B, required and absolute.
- Project names are slugified before use (lowercase, `[a-z0-9-]` only); `Path.resolve()` containment check ensures the resolved subdir is inside `<wiki_dir>` before any write.

### Env vars

| Var | Purpose | Default |
|---|---|---|
| `ASOF_DIR` | Override wiki directory location | `~/.claude/asof` |
| `ASOF_PROJECT_ROOT` | Used by the change-reminder hook to know which project root to check | (unset → hook no-ops) |
| `ASOF_NON_INTERACTIVE` | If set to `1`, all skills run with prompts disabled (CI / scripted setups) | unset |

## 6. Skill specs

### 6.1 `asof:init` — interactive preflight + bootstrap wizard

```yaml
---
name: init
description: Bootstrap a new time-aware wiki for a project. Runs a preflight check (Python, rsync, Obsidian optional), asks where the wiki should live (shared / per-project / inside-repo), creates the wiki directory, seeds the schema, registers the project, and optionally installs the proactive change-reminder hook + project CLAUDE.md snippet. Use when starting a wiki for a new codebase, research topic, or knowledge base.
when_to_use: Trigger phrases — "init the wiki", "set up the wiki", "create a wiki for this project", "bootstrap an asof wiki", "start tracking docs over time for X".
disable-model-invocation: true
allowed-tools: Bash(python3 *) Bash(mkdir *) Bash(cp *) Bash(test *) Bash(which *) Bash(rsync --version) Bash(git --version) Read Write Edit
argument-hint: "[project-name] [source-path] [--non-interactive] [--yes] [--dry-run] [--import-existing <path>]"
---
```

**Modes:**
- Interactive (default) — five-stage wizard (described below).
- `--non-interactive` / `--yes` — accepts every prompt with the documented default; required for CI and scripted setups. Combined with sane defaults this is enough to run an entire `init` without human input.
- `--dry-run` — prints what would be created / modified, makes no filesystem changes.
- `--import-existing <path>` — bootstraps from an existing wiki dir (e.g. brain-sync's `~/Desktop/Brain/` with `.brain-sync.json`). Migrates the project list to `.asof.json` format, preserves all existing wiki pages.

Project name validation runs on every invocation: lowercase `[a-z0-9-]` only, no path separators, refused if it would resolve outside `<wiki_dir>` after `Path.resolve()`.

`init` is **interactive** — the agent walks the user through five stages:

#### Stage 1: preflight check

Runs `scripts/init.py --preflight` first. Checks:

| Check | Required? | Action if missing |
|---|---|---|
| `python3 --version` ≥ 3.9 | **Required** | Block: "Install Python 3.9+ via Homebrew or pyenv: `brew install python@3.12`" |
| `rsync --version` exists | **Required** | Block: "Install rsync: `brew install rsync` (macOS) — already on most Linux" |
| `git --version` exists | **Recommended** | Warn: "git isn't required to run, but you'll want it for versioning the wiki" |
| `obsidian` app present (`/Applications/Obsidian.app` or PATH) | **Optional** | Inform: "Obsidian not detected. The wiki is plain markdown, so any editor works, but Obsidian gives you graph view, backlinks, and frontmatter queries. Get it from obsidian.md" |
| `uv`, `pip`, `node`, etc. | **Not required** | Explicitly: "asof has zero Python or Node dependencies — stdlib only. No `uv`, no `pip install`, no `npm i`." |

Print a clean table of what's present / missing. Exit non-zero on **Required** failures with the install hint.

#### Stage 2: pick wiki layout

Ask the user to choose A / B / C with one-sentence explanations of each:

- **A. Shared wiki, all projects under `~/.claude/asof/` (default).** One Obsidian vault, browse everything together. Recommended for solo users with multiple related projects.
- **B. Per-project wiki, but under home (`~/.claude/asof-<name>/` or custom path).** Each project is its own vault, independent. Recommended when projects are for different audiences (work / personal).
- **C. Wiki inside the source repo (`<repo>/.asof/`), versioned with code.** Travels with the repo via git. Recommended for open-source projects and teams.

Capture the choice + a custom path if they pick B/C-with-non-default.

#### Stage 3: create the wiki

If the wiki dir doesn't exist:
- Create `raw/`, `wiki/`.
- Write `<wiki_dir>/CLAUDE.md` from `templates/wiki_root_CLAUDE.md` (with the schema spec referenced, not duplicated).
- Write `<wiki_dir>/.asof.json` with `schema_version: "1.0"` and the new project block.

If the wiki dir exists but is uninitialized (no `.asof.json`):
- Detect, ask if the user wants to initialize in place (might pick up an existing `~/Desktop/Brain/`-style setup — this is the migration path).

If the wiki dir exists and is initialized:
- Refuse to overwrite. Print: "Project `<name>` already exists in `<wiki_dir>/.asof.json`. To add a different project, run `/asof:init <other-name>`. To re-bootstrap, delete the entry manually."

#### Stage 4: scaffold project pages

Create `wiki/<project>/{index,log,_candidates,current_state}.md` from `templates/`, with `{{PROJECT_NAME}}` substituted.

#### Stage 5: integrations (4 yes/no questions)

1. **Append the wiki-precedence snippet to `<source>/CLAUDE.md`?**
   - Refuses if the snippet's heading already exists; tells the user where to merge manually.
2. **Install the PostToolUse change-reminder hook into `<source>/.claude/settings.json`?**
   - Writes the hook into `<repo>/.claude/hooks/wiki_change_reminder.py` (parameterized via `ASOF_PROJECT_ROOT` and `ASOF_DIR` env in settings.json).
   - Merges into existing `settings.json` if present; never overwrites.
3. **Add `<wiki_dir>` to the project's `additionalDirectories` so the agent can read the wiki when working in this repo?** (Patterns A and B only — Pattern C doesn't need this since the wiki is in the repo.)
   - Edits `.claude/settings.json` (or `.claude/settings.local.json` if user prefers).
4. **Run a first sync now?** If yes → invoke `/asof:sync <project>`.

**Partial-failure handling (idempotent):** each integration step runs as an independent transaction. If hook install fails (e.g. malformed existing settings.json), the wizard continues to the next question with a recorded warning; the final summary surfaces what succeeded and what didn't with concrete recovery commands. Re-running `init` resumes only the failed steps; successful ones are detected and skipped.

Echo a final summary: what was created, what was skipped, the four next-step commands the user can run.

### 6.2 `asof:sync`

Direct port of today's `brain-sync` with the rglob bug fix and the following design changes from Codex review:

- **Symlink handling.** rsync defaults to `--safe-links` (skip symlinks that point outside the source tree) to avoid path-escape and recursion surprises. The current brain-sync skips symlinks entirely (treating them as aliases of their targets); v1 preserves this behavior with explicit documentation. `--copy-links` is offered as an opt-in for users who want symlinks resolved into real files in `raw/`. Codex Phase-1 advice round 3.
- **Project selection is cwd-aware, not "all by default".** With no argument, sync auto-selects the project whose `source` contains `cwd`. Multiple matches → prompt. No matches → refuse with `"specify --project <name> or --all"`. Explicit `--all` flag syncs everything.
- **Per-project last-sync reports.** Replaced the global `<wiki_dir>/.last-sync.json` with `<wiki_dir>/.last-sync/<project>.json` — atomic writes, no clobbering across projects.
- **Self-ingest hard guard.** If `wiki_dir` is detected inside `source` (Pattern C) and excludes don't include `.asof`, sync aborts with a clear error before any rsync runs. `--allow-self` overrides for power users.
- **File locking.** `fcntl.flock` on `<wiki_dir>/.asof.lock` for the duration of a sync. Concurrent invocations queue cleanly; the change-reminder hook backs off when the lock is held (just emits the reminder, no rsync attempt).
- **`rglob` recursion fix** (already applied locally; in the initial commit).
- **Flags:**
  - `--project <name>` — explicit project selection.
  - `--all` — explicitly sync every configured project (replaces today's "no arg = all").
  - `--summary-only` — skip listing files past N=60, just count.
  - `--strict-mtime` — fail on mtime regressions instead of silently re-ingesting (off by default).
  - `--dry-run` — runs delta detection but no rsync, no `.last-sync/` writes.
  - `--non-interactive` — accepts default for any prompt (CI mode).
  - `--allow-self` — override the self-ingest guard (rarely needed).

```yaml
---
name: sync
description: Sync source repo .md files into the wiki's raw/ dir and detect what changed. Use when the user says "sync the wiki", "asof sync", "update the wiki", "ingest new docs", "refresh the wiki from <project>".
when_to_use: Trigger phrases — sync the wiki, asof sync, update the wiki, refresh the wiki, ingest new docs, bring the wiki up to date, pull updates from <project>.
allowed-tools: Bash(rsync *) Bash(stat *) Bash(find *) Bash(python3 *) Bash(grep *) Bash(diff *) Bash(ls *) Bash(cat *) Bash(jq *) Bash(test *) Read Write Edit
argument-hint: "[project-name (optional)] [--all] [--dry-run] [--summary-only] [--strict-mtime] [--non-interactive]"
---
```

SKILL.md body stays under 200 lines: trigger surface + high-level flow. Detailed ingest procedure lives in `references/INGEST_PROCEDURE.md`, loaded on demand.

### 6.3 `asof:lint`

```yaml
---
name: lint
description: Audit the wiki for schema violations — stale claims, missing supersession notes, orphan pages, broken source paths. Use when the user says "lint the wiki", "audit the wiki", "asof lint", "check wiki health", or after a large ingest session.
allowed-tools: Bash(python3 *) Bash(grep *) Bash(find *) Read
argument-hint: "[project-name (optional)] [--fix]"
---
```

`scripts/lint.py` runs 7 checks:
1. **Mtime drift** — pages where `last_updated` is more than `lint_thresholds.mtime_drift_days` older than newest cited `source_mtime`. Default 30, configurable per wiki via `.asof.json`.
2. **Supersession gap** — pages citing two sources whose `source_mtime` differ by `lint_thresholds.supersession_gap_days`+ with no supersession note in body. Default 60, configurable per wiki.
3. **Missing mtime** — source-summary frontmatter without `source_mtime` (data quality bug).
4. **Orphan pages** — pages with no inbound link from `index.md` or any other wiki page.
5. **Removed-source claims** — pages with `<!-- backing source removed -->` markers.
6. **Path mismatch** — source-summaries whose `path:` doesn't actually exist under `raw/`.
7. **Frontmatter validity** — missing required fields per schema (`title`, `type`, `last_updated`).

Output: structured report grouped by severity. `--fix` flag for the safe ones (rewrite `last_updated`, add missing `_index.md` entries — never edit content). Holds the same `<wiki_dir>/.asof.lock` as sync to prevent races.

**Read-only mode interaction (compat-matrix cell b):** when the skill version is in the read-only window (`min_reader_version ≤ skill_version < min_writer_version`), `lint` runs *report-only*. `--fix` is **rejected** with a clear "read-only mode — upgrade asof to ≥ `<min_writer_version>`" message. The lint report itself is unaffected; only the auto-repair side-effect is blocked.

## 7. Schema spec (`references/SCHEMA.md`)

Single source of truth, sections:

1. **Folder layout** — `raw/` is a **rsync-managed mirror of source `*.md` files** (agent-read-only — only sync writes here, only via rsync). `wiki/` is **LLM-owned**, mirrors the source structure. Persistence of upstream-deleted sources is preserved in the wiki via `removed_upstream:` markers, not by keeping deleted files in `raw/`.
2. **Required frontmatter** — full field list + types.
3. **Time-aware ingest rules** — read mtime first, conflict resolution by recency, current-state pages.
4. **Page types** — `source-summary`, `entity`, `concept`, `comparison`, `overview`, `synthesis`.
5. **Log format** — greppable `## [date] action | source` prefix.
6. **Lint rules** — the 7 above (thresholds configurable per wiki).
7. **Bookkeeping files** — `index.md`, `log.md`, `_candidates.md`.
8. **Candidate-promotion threshold** — currently 3 mentions; documented explicitly.
9. **Schema version compatibility** — `schema_version`, `min_reader_version`, `min_writer_version` semantics. Migration policy (additive-only between minor versions; breaking changes only on major bumps with migration scripts that update shipped examples in the same PR; pre-migration `wiki/` backup mandatory). See section 17.

The phrase "raw is immutable" (used in the original draft and in earlier brain-sync docs) is **deprecated** — it confused contributors into thinking deletes were forbidden. The accurate policy is: *only the agent never modifies `raw/`*; rsync `--delete` is allowed and expected, and the wiki preserves history of deleted sources via `removed_upstream:` markers in source-summaries.

Karpathy's original prose lives separately at `references/KARPATHY_PATTERN.md` for context/attribution but isn't part of the operative rules.

## 8. Hook template (`templates/hooks/wiki_change_reminder.py`)

PostToolUse hook that fires on `Write` / `Edit` / `MultiEdit` / `NotebookEdit` of `*.md` files inside the project. The hook contract is the **verified** Claude Code primitive for non-blocking context injection: **exit 0 with JSON `hookSpecificOutput.additionalContext`**.

### Why not exit-2 + stderr (our initial design)

The official docs reserve exit-2-with-stderr for **errors** in PostToolUse — Claude sees the message but treats it as something that went wrong. Using it for a benign reminder abuses the semantics. JSON `additionalContext` is the documented mechanism: appears next to the tool result, is saved in the transcript, and survives `--continue` / `--resume`.

### Hook output shape

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "Wiki at /Users/jcarr/.claude/asof/ may now be stale for docs/foo.md. Consider /asof:sync."
  }
}
```

Constraints from the docs:
- **10,000-char cap per call.** The reminder is one-line so this is irrelevant in practice; documented for awareness.
- **No coalescing across parallel tool calls.** A 50-file `MultiEdit` fires 50 PostToolUse hooks in parallel. Without protection the user gets 50 identical reminders. Solved via the debounce below.

### Debounce mechanism (per-project)

Hook checks `<wiki_dir>/.pending-sync/<project>.stamp` before emitting (per-project, not global — Codex review round 2: a global debounce file would let a write in project A suppress reminders for project B in shared wikis):

1. If the per-project stamp file's mtime is within the last 30 seconds, exit 0 with no JSON output (silent — Claude sees nothing).
2. Otherwise, `touch <wiki_dir>/.pending-sync/<project>.stamp` and emit the JSON reminder.

Result: the first `.md` write in a refactor of project A surfaces the reminder; the next 49 within the same 30 s window are silently suppressed *for project A only*. A simultaneous edit in project B emits its own reminder, undeduped against A's.

The hook resolves project from `ASOF_PROJECT_ROOT` (set by `init` in `.claude/settings.json`'s `env` block) and `ASOF_PROJECT_NAME` (set alongside it). Both must be set; if either is unset, the hook silently no-ops.

### Hook environment

- `ASOF_PROJECT_ROOT` — set in `.claude/settings.json` `env` block by `asof:init`. The hook only fires for paths under this root (otherwise no-ops cleanly).
- `ASOF_PROJECT_NAME` — set alongside `ASOF_PROJECT_ROOT`. Used to scope the debounce stamp file.
- `ASOF_DIR` — wiki dir for the debounce file location.
- If any of these env vars are unset, the hook silently no-ops (defensive default).
- If `<wiki_dir>/.asof.lock` is held (a sync is running), the hook still emits the reminder but adds *"(sync in progress, your changes will be picked up)"* to the message.

### Init integration

`asof:init` writes a parameterized `<source>/.claude/hooks/wiki_change_reminder.py` and merges a PostToolUse entry into `<source>/.claude/settings.json` (`env` block sets `ASOF_PROJECT_ROOT`, `ASOF_PROJECT_NAME`, and `ASOF_DIR`). Existing settings.json is preserved; never overwritten.

## 9. Requirements summary (for the README)

Single canonical list, surfaced in three places: README, `init` preflight, FAQ.

| Component | Required? | Why | How to install |
|---|---|---|---|
| **Python 3.9+** | Required | All helper scripts | `brew install python@3.12` (macOS), system Python on Linux usually fine |
| **`rsync`** | Required | Source → raw mirroring | macOS: `brew install rsync` (or builtin BSD rsync works for our flags). Linux: usually preinstalled |
| **Claude Code** | Required | The skill runtime | `claude.ai/code` |
| **`git`** | Recommended | Versioning the wiki, especially Pattern C | macOS: comes with Xcode CLI tools. Linux: package manager |
| **Obsidian** | Optional | Best UX for browsing the wiki (graph view, backlinks, frontmatter queries) | `obsidian.md` |
| **`uv`, `pip`, `node`, etc.** | Not used | We're stdlib-only by design | — |

The README opens with: *"Zero Python deps. Zero Node deps. One Python script per skill, all stdlib. If you have Python 3.9+ and rsync, you're done."*

## 10. Production-readiness checklist

| Concern | Plan |
|---|---|
| **Tests** | `tests/` with pytest. Fixtures: tiny synthetic wiki + raw setup. Cover the four bugs we hit (`rglob` recursion, mtime regressions counted as MODIFIED, nested frontmatter, `--delete` corner cases) **plus** Codex-flagged regressions: Pattern C self-ingest blocked, project-name path-traversal rejected, sync default selects by cwd, file-lock contention serializes correctly, schema-version compat boundary respected. CI via GitHub Actions on macOS + Linux. |
| **Idempotency** | Re-running `asof:init` on an initialized wiki is a no-op with a helpful message. Re-running `asof:sync` with no changes prints "wiki is up to date" and exits 0. Partial-failure recovery in `init` re-runs only the missing integrations. |
| **Concurrency** | `fcntl.flock` on `<wiki_dir>/.asof.lock` for sync and lint. Hook backs off (still emits reminder, no rsync) when the lock is held. Per-project `.last-sync/<project>.json` writes are atomic (write-temp-then-rename). |
| **Error messages** | Every error says what went wrong, where to look, what to do. No silent failures (the original `glob` bug returned empty silently for two months). |
| **Input sanitization** | Project names slugified (`[a-z0-9-]` only). `Path.resolve()` containment check before any write — refuse if the resolved path escapes `<wiki_dir>`. Same check for `--wiki-dir` arg. |
| **Cross-platform** | macOS-first (Claude Code's primary platform), Linux-tested. Avoid `stat -f` (BSD); use Python's `pathlib.Path.stat()`. CI matrix runs both. |
| **No required deps** | Python 3.9+ stdlib only. `rsync` is the only system binary required (already on macOS / most Linux). |
| **Schema versioning** | Full four-cell compat matrix (section 2): refuse / read-only / require `--migrate` / work-as-is. **Never auto-migrate silently.** Migrations always preceded by automatic `wiki/` → `wiki.bak.<timestamp>/` backup; rollback is a `mv` away. **CI gates** in `.github/workflows/`: (a) lint all three shipped examples on every PR; (b) detect `schema_version` bumps in `references/SCHEMA.md` and fail the PR if no migration script in `migrations/<from>_to_<to>.py` and no example updates accompany it. |
| **Dry-run support** | `--dry-run` on `init` and `sync` makes no filesystem changes; prints exactly what would happen. Required for safe inspection before destructive ops. |
| **Non-interactive support** | `--non-interactive` / `--yes` flags + `ASOF_NON_INTERACTIVE=1` env. Defaults pre-documented for every prompt. CI / scripted setup just works. |
| **Docs** | README with: 30-second pitch, install (3 ways), 5-minute tutorial (init → first sync → first lint), asciinema demo, FAQ ("why not RAG?", "non-code projects?", "Codex / OpenCode compatibility?"). |
| **Examples** | Three small example wikis: codebase, research topic, book. Each has 3-5 source files + resulting wiki. **CI runs `asof:lint` over all three on every PR** so schema evolution can't silently break them. Demos > docs. |
| **Anti-patterns avoided** | Skill descriptions follow the doc's advice: key use case first, trigger phrases up front, combined description + when_to_use under 1,536 chars. |
| **Hook safety** | Ships disabled-by-default; opt-in during `init`. Project-scope only. Uses verified PostToolUse + JSON `additionalContext` contract (not exit-2 abuse). Debounced via **per-project** `<wiki_dir>/.pending-sync/<project>.stamp` to handle parallel-fire from `MultiEdit` *without cross-project suppression*. |

## 11. README design (must feel: simple, automatic, obviously useful)

The README is the conversion surface. The user's reaction in the first 30 seconds should be **"oh, that's it? I'll try it."** Not "let me read a tutorial first."

### Tone rules

- **No marketing speak.** No "powerful", "blazingly fast", "AI-powered". The thing speaks for itself.
- **Short paragraphs.** Two-line max in the hero section. Five-line max anywhere.
- **Show, don't tell.** Every claim is backed by a screenshot, a code block, or an asciinema.
- **One command per step.** If the install reads like more than 3 commands, it has failed.
- **Address objections inline, not in a wall-of-text FAQ.** People skim.

### First screen (above the fold)

```markdown
# asof

**A time-aware wiki for Claude Code. Older claims age, newer ones supersede, the wiki tells you what's true *as of now*.**

[asciinema demo here — 30-second sync showing supersession in action]

## Install in one command

claude plugin add github.com/<user>/asof

That's it. Now type `/asof:init` in any Claude Code session — a guided wizard does everything else.
```

That's the entire above-the-fold. Three lines of pitch, one demo, one install command, one next step. If the user reads nothing else they should already know what it does and how to start.

### Below the fold (in order)

1. **What you get** — 3 bullets max, each with a one-line example:
   - *Time-aware: every wiki claim is tagged with the source's mtime; newer sources supersede older ones automatically.*
   - *Self-correcting: the agent re-ingests deltas (new / modified / deleted source files) using documented supersession rules.*
   - *Multi-project: one wiki dir holds N projects with hard subdirectory isolation; or one wiki per repo if you prefer.*

2. **Why this exists** — two paragraphs, with a "before / after" snippet showing how a question gets answered without asof (RAG re-derives every time) vs. with asof (the wiki already knows).

3. **5-minute tour** — three commands with their effects:
   ```
   /asof:init my-project ~/code/my-project
   ```
   *Walks you through preflight (Python? rsync? Obsidian?), wiki layout choice (shared / per-project / inside-repo), and integrations.*

   ```
   /asof:sync my-project
   ```
   *Pulls .md files from your repo into the wiki's raw/ dir. Detects what changed. The agent re-ingests deltas with you.*

   ```
   /asof:lint
   ```
   *Audits the wiki for stale claims, missing supersession notes, orphan pages.*

4. **Three layouts, pick one (or let `init` ask you)** — A/B/C diagrams, one paragraph each.

5. **Requirements** — a 5-line table. *"Python 3.9+, rsync, that's it. Zero pip / npm / uv. Obsidian recommended but optional — the wiki is just markdown."*

6. **What the agent does for you** — short narrative of a sync session: file changes detected → agent reads new content → updates entity pages → records supersession → updates index → appends log entry. *"You read the diff and approve."*

7. **Pre-empted questions** — addressed inline as section breaks, not as a footer FAQ:
   - *"Why not RAG?"* (1 paragraph: RAG re-derives on every query, asof compounds.)
   - *"Does it work for non-code projects?"* (Yes — research, books, business KBs. Show the `examples/` dir.)
   - *"Codex / OpenCode compatibility?"* (Skills follow the open Agent Skills standard. Verify per release.)
   - *"Multi-user?"* (v1 is single-user. The wiki is git, so async collaboration via PRs works fine; concurrent live editing doesn't.)

8. **For existing brain-sync users** — one paragraph + one command for the migration path.

9. **Contributing / License / Credits** — short. Karpathy's pattern attributed.

### Visual assets to ship with v1.0

| Asset | Format | What it shows |
|---|---|---|
| `docs/demo.cast` | asciinema | 30-second `init` → `sync` → answer-a-question loop |
| `docs/screenshots/obsidian-graph.png` | PNG | Obsidian graph view of an example wiki — the visual payoff |
| `docs/screenshots/sync-output.png` | PNG | Terminal screenshot of a sync's NEW/MODIFIED/DELETED report |
| `docs/screenshots/supersession.png` | PNG | A wiki page with a "Previously X, superseded by Y" note rendered in Obsidian |

asciinema is preferred over GIFs — copy-pasteable from the terminal recording.

### Anti-patterns to avoid in the README

- **"Architecture diagrams" before install.** People want to install first, understand later.
- **Long FAQ at the bottom.** Bury answers inline near the relevant section.
- **Claiming "supports X, Y, Z" in the hero.** State the one core thing; the long list goes in a "What you get" subsection.
- **Asking the user to think.** Every section ends with a concrete next command.

### Distribution-relevant copy

The hero command should adapt to whatever Claude Code's actual plugin-install syntax becomes. v1.0 README has both:

```bash
# If your Claude Code supports plugins:
claude plugin add github.com/<user>/asof

# Manual install (works on any Claude Code version):
git clone github.com/<user>/asof ~/.claude/skills/asof
```

`init` then takes over — no manual `~/.claude/settings.json` editing, no `additionalDirectories` step the user has to remember. The skill handles all of it via the preflight wizard.

## 12. Distribution

Three install paths, all in README:

1. **Plugin install** (preferred). Add the repo as a plugin source via Claude Code's plugin mechanism. One command to install + update.
2. **Manual personal install**. `git clone` into `~/.claude/skills/asof/` — but because the repo holds skills not a plugin shape, the user gets the skills loose without the `asof:` namespace prefix. Slightly less clean.
3. **Manual project install**. `git submodule add` into `<repo>/.claude/skills/asof/`. For teams that want the wiki tied to a single repo.

`scripts/install.sh` automates path 2 and runs `asof:init` interactively.

## 13. Migration for existing user (me)

The current setup: `~/Desktop/Brain/` (wiki dir), `~/.claude/skills/brain-sync/` (current skill), `.brain-sync.json` (config).

Migration drama-free:
- `asof:init --import-existing ~/Desktop/Brain/` reads `.brain-sync.json`, copies the project list into the new `.asof.json` format under the existing dir (so `<wiki_dir>` = `~/Desktop/Brain/` for me), and writes the schema-version field.
- Old `brain-sync` skill at `~/.claude/skills/brain-sync/` keeps working since names don't collide (`brain-sync` vs `asof`). Delete it once verified.
- Hook in the project we already wired keeps working — only its file path needs updating if I move it inside the plugin's templates dir.

## 14. Resolved questions and remaining open ones

**Resolved by Codex round 1 + verification:**
- ✅ **Hook contract.** PostToolUse + exit-0 + JSON `hookSpecificOutput.additionalContext` (verified against official docs). Switched from exit-2 + stderr.
- ✅ **Sync default in shared mode.** cwd-aware auto-select; prompt on multi-match (interactive); fail-fast on multi-match (non-interactive, unless `--auto-select-longest`); refuse with `--project` or `--all` on no-match.
- ✅ **Pattern C source path.** Auto-derived as parent of `.asof/` — never stored in committed config.
- ✅ **Examples count.** All three (codebase, research, book) ship in v1.0 with mandatory CI lint coverage.
- ✅ **License.** MIT.
- ✅ **`raw/` mutability semantics.** It's a mirror — agent-read-only but rsync-managed; persistence via `removed_upstream:` in wiki source-summaries.
- ✅ **Synthesis auto-trigger.** Default v1: always confirm before synthesis writes (>0 delta = pause for user).

**Resolved by Codex round 2:**
- ✅ **Pattern C `wiki_dir` portability.** Committed `.asof.json` for Pattern C omits `wiki_dir` entirely; the wiki dir is the directory containing the config file (resolved at load time). Section 5 has separate Pattern A/B and Pattern C config examples.
- ✅ **Version-compatibility matrix.** Four-cell matrix covers refuse / read-only / require-`--migrate` / work-as-is. Never auto-migrate. See section 2.
- ✅ **Non-interactive multi-match behavior.** Fail-fast unless `--project` / `--all` / opt-in `--auto-select-longest`.
- ✅ **Hook debounce per-project.** `<wiki_dir>/.pending-sync/<project>.stamp` instead of a global file. New env var `ASOF_PROJECT_NAME` set by `init`.
- ✅ **Schema-discipline mechanical enforcement.** Two CI jobs in `.github/workflows/schema.yml`: `lint-examples` (every PR) and `schema-bump-gate` (PR fails if `schema_version` bumped without migration script + example updates).
- ✅ **Newer skill / older wiki.** Works normally, no migration. Explicit cell (d) in the compat matrix.

**Still open:**
1. **Plugin manifest format.** The Claude Code skills doc references plugins but doesn't fully specify the manifest. Read `/en/plugins` next to confirm. If plugins aren't first-class for distribution yet, ship as path-2 (personal install via git clone) and add plugin metadata when the marketplace stabilizes.
2. **Search.** Karpathy mentions `qmd` for full-text/vector search. Skip in v1; add as optional `asof:search` skill in v2 if there's demand.
3. **Multi-user collaboration.** v1 is single-user; document explicitly.
4. **Codex / OpenCode parity.** The skills doc says skills follow the open Agent Skills standard. Verify the plugin runs in those tools by spec-checking; if it does, README mentions it.

## 15. Implementation phases

Each phase shippable on its own:

| Phase | Output | Why |
|---|---|---|
| **0** | Repo scaffold (this PLAN.md, README, LICENSE, CHANGELOG, plugin.json placeholder, empty skill dirs). | Anchor point. |
| **1** | `asof:sync` skill — port today's brain-sync with rglob fix, $ARGUMENTS wiring, JSON delta report, doc cleanup. | Most-used path; ship it first so existing workflow benefits immediately. |
| **2** | `references/SCHEMA.md` extracted from current `~/Desktop/Brain/CLAUDE.md`, generalized away from Karpathy prose. + templates. | Required for both `init` and `lint`. |
| **3** | `asof:init` skill — bootstrap, config writer, optional hook + CLAUDE.md snippet integration. | Lets new users start. |
| **4** | `asof:lint` skill — pure read-side, separate from sync. | Lets us audit existing wiki and example wikis before v1.0. |
| **5** | Tests (pytest + fixtures) — codify the four bugs we hit. | Before public release. |
| **6** | `examples/` — three small wikis (codebase, research, book). | Demos > docs. |
| **7** | **README v1** — written *during* phase 1 (not at the end). Skeleton + the core "Install in one command" pitch lands with the first working `sync` skill. Updated after each phase to keep showing what's actually working. | The README is the conversion surface; treat it as a first-class artifact, not polish. |
| **8** | **README v1.0 final** — asciinema demo, screenshots (Obsidian graph view, supersession in rendered MD, sync output), examples linked. Install verified end-to-end on a clean macOS + clean Linux box. **v1.0 tag.** | Public release. |
| **9** *(optional)* | Plugin manifest + marketplace submission once that's a thing. v1.1. | Distribution upgrade. |

Phase 1 alone unblocks the existing user — replaces the current `brain-sync` with a generic, tested version.

## 16. Next action

Phase 0: scaffold the repo (README skeleton, LICENSE, gitignore, empty skill dirs, plugin.json placeholder). After that, Phase 1 (`asof:sync` skill) is the first real working artifact.

## 17. Schema evolution discipline

Codex flagged that shipping multiple examples is "load-bearing on the schema" — every shipped example is encoded per the v1.0 schema, so any schema change ripples to all of them. We accept the multi-example ship for v1.0 *because we commit to discipline that bounds the maintenance cost*:

### Three-tier change classification

| Change tier | Examples | Required action |
|---|---|---|
| **Additive** (backwards-compatible) | New optional frontmatter field; new optional page type; new lint rule that's off-by-default | Bump minor version. Examples need no update — they're still valid. |
| **Lint rule promoted to default** | A previously-optional check now runs by default | Bump minor version. Examples must pass the new check; CI catches breakage on PR. |
| **Breaking** | Required field changed; existing field's meaning changed; page type renamed | Bump major version. Migration script ships in same PR. The script updates the three shipped examples *and* is documented as the user-facing migration path. CI runs the migration on examples and lints; PR is blocked until both pass. |

### Rules

1. **Additive-only between minor versions.** Anything that would invalidate an existing wiki page is a major-version change. No exceptions for "small breaking changes".
2. **Migration scripts ship with the PR.** A breaking change without a migration script is not mergeable. The script updates examples in the same PR.
3. **CI lints all shipped examples on every PR.** Any lint failure blocks the merge. Schema changes that would silently break examples are caught here.
4. **Pre-migration backup is mandatory.** Skill copies `wiki/` to `wiki.bak.<timestamp>/` before running any migration. Rollback is `mv wiki/ wiki.broken && mv wiki.bak.* wiki/`.
5. **`min_reader_version` enforces forward-incompat.** A wiki on schema 2.0 with a 1.0 skill refuses to load with a clear message ("upgrade asof to ≥ 2.0").
6. **CHANGELOG.md is the source of truth for migrations.** Every breaking change has a CHANGELOG entry that names the migration script and what it does.

### Effect on examples

With this discipline, the maintenance cost of three examples is **roughly the same as one** for the lifetime of v1.x. Examples become a marketing asset (proves "domain-agnostic") and a regression tripwire (CI lint catches schema-drift bugs) — at near-zero ongoing cost.

### Mechanical CI enforcement (Codex round 2)

The discipline is *aspirational* without CI gates. Two concrete jobs in `.github/workflows/schema.yml`:

```yaml
jobs:
  lint-examples:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python3 skills/lint/scripts/lint.py --wiki-dir examples/codebase-wiki --fail-on-warn
      - run: python3 skills/lint/scripts/lint.py --wiki-dir examples/research-wiki --fail-on-warn
      - run: python3 skills/lint/scripts/lint.py --wiki-dir examples/book-wiki --fail-on-warn

  schema-bump-gate:
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 2 }
      - run: |
          # If schema_version bumped in references/SCHEMA.md, require a migration script
          # in migrations/<from>_to_<to>.py and at least one example to be touched in this PR.
          python3 scripts/ci/check_schema_bump.py --base ${{ github.event.pull_request.base.sha }}
```

`scripts/ci/check_schema_bump.py` — small (<100 lines) script that diffs `schema_version` between PR base and head, looks for a corresponding migration script, and fails the PR if absent. Same script also asserts that any breaking-tier change includes example updates (touch counts in `examples/*-wiki/`).

These two gates make the discipline real, not aspirational.
