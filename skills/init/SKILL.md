---
name: init
description: "Bootstrap a time-aware asof wiki for a project. CRITICAL — when invoked with any required positional arg missing (project_name or source_path), the agent MUST gather missing values via the AskUserQuestion tool, never via prose Q&A in chat. See the AGENT CONTRACT at the top of the body. Five-stage interactive wizard — preflight checks, layout choice (shared/per-project/in-repo), wiki dir + config creation, project page scaffold, integrations. Use when starting a wiki for a new codebase, research topic, business KB, book club, or any markdown corpus that should be remembered with mtime semantics."
when_to_use: "Trigger phrases — 'init the wiki', 'asof init', 'set up the wiki', 'create a wiki for this project', 'bootstrap an asof wiki'. When args are missing, the agent MUST call AskUserQuestion (project_name, source_path, pattern, first-sync) — never numbered prose Q&A. The body's AGENT CONTRACT spells out the exact required call."
disable-model-invocation: true
allowed-tools: Bash(python3 *) Bash(mkdir *) Bash(cp *) Bash(test *) Bash(which *) Bash(rsync --version) Bash(git --version) Bash(stat *) Read Write Edit AskUserQuestion
argument-hint: "[project-name] [source-path] [--pattern A|B|C] [--wiki-dir PATH] [--non-interactive] [--yes] [--dry-run] [--no-install-hook] [--no-claudemd-snippet] [--no-additional-directories] [--skip-first-sync] [--commit-settings]"
---

# asof:init

> ## ⚠️ AGENT CONTRACT — READ BEFORE INVOKING THE SCRIPT
>
> When the user invokes `/asof:init` with **any** required positional arg missing (`project_name` and/or `source_path`), you (the agent) **MUST** gather the missing values via Claude Code's `AskUserQuestion` tool — NOT via prose Q&A in the conversation.
>
> **REQUIRED behavior** (one batched `AskUserQuestion` call, multiple questions):
>
> ```
> AskUserQuestion(questions=[
>   {
>     question: "What project name should I use for the asof wiki?",
>     header: "Project name",
>     multiSelect: false,
>     options: [
>       { label: "<cwd-basename> (Recommended)", description: "Slug derived from the current directory's name." },
>       // The user types a custom name via the auto-injected "Other" option.
>     ]
>   },
>   {
>     question: "Which directory should the wiki track as its source?",
>     header: "Source path",
>     multiSelect: false,
>     options: [
>       { label: "<cwd> (Recommended)", description: "Track the whole current project tree." },
>       // If cwd has an obvious-source subdir (docs/, src/, notes/, papers/),
>       // add it as a second labeled option.
>     ]
>   },
>   {
>     question: "Which wiki layout pattern?",
>     header: "Wiki layout",
>     multiSelect: false,
>     options: [
>       { label: "Pattern A — shared (Recommended)", description: "Wiki at ~/.claude/asof/, multiple projects share it." },
>       { label: "Pattern B — per-project under home", description: "Wiki at ~/.claude/asof-<project>/." },
>       { label: "Pattern C — in-repo .asof/", description: "Wiki at <repo>/.asof/, committed alongside code." }
>     ]
>   },
>   {
>     question: "Run an initial sync after init?",
>     header: "First sync",
>     multiSelect: false,
>     options: [
>       { label: "Yes (Recommended)", description: "Mirror your sources into raw/ now." },
>       { label: "No, I'll run /asof:sync later", description: "Skip the first sync — bootstrap only." }
>     ]
>   }
> ])
> ```
>
> **PROHIBITED behavior** (do NOT do this — violates the skill contract):
>
> - ❌ Listing options as numbered prose ("1. project_name = X? 2. source_path = Y?") and asking the user to type back.
> - ❌ Auto-running with assumed defaults + a follow-up "say go to confirm" prose question.
> - ❌ Doing a `--dry-run` first as a substitute for asking — the user wanted a structured choice, not a preview-then-confirm prose loop.
> - ❌ "Two paths forward — which do you want: 1... 2..." prose menus.
>
> After `AskUserQuestion` returns the user's selections, invoke the script with `--non-interactive` (since you've already collected every choice) plus the chosen args. Default the un-asked integrations (hook, CLAUDE.md snippet, additional-directories) to ON; these are correct for ~99% of users and asking again is friction without value.
>
> **Why this is mandatory:** prose Q&A makes users type yes/no/path-strings into chat (slow, error-prone, no "Recommended" visual). `AskUserQuestion` shows a clickable list with "(Recommended)" tags accepted in one keystroke. For a one-time bootstrap action that the user will only do once per project, the structured UI is the difference between "delightful" and "ugly."

Bootstrap a new asof wiki for a project. Five-stage interactive wizard backed by independent Python modules (`preflight`, `wizard`, `scaffold`, `integrations`). Designed to be deliberate (one-time per project) and idempotent (re-runs detect existing state and skip).

## How to invoke

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/init.py $ARGUMENTS
```

Both positional args (`project_name`, `source_path`) are required. Everything else is optional with documented defaults. **If args are missing, follow the AGENT CONTRACT above before running the script.**

## What the script does, in five stages

### Stage 1 — preflight

System dependency check. Required: Python 3.9+, rsync. Recommended: git. Optional: Obsidian. Plus an explicit informational note that **no pip / npm / uv / node** is required at runtime — asof is stdlib-only.

Exits with code 2 if any required dependency is missing, with actionable install hints in the `Notes:` section of the output table.

### Stage 2 — wiki layout

Three patterns supported (PLAN.md section 4):

- **Pattern A**: shared wiki, multiple projects, default `~/.claude/asof/`. Recommended for solo users with several related projects.
- **Pattern B**: per-project wiki under home (e.g. `~/.claude/asof-myproject/`). Recommended when projects are for different audiences (work / personal).
- **Pattern C**: wiki inside the source repo at `<repo>/.asof/`, committed alongside code. Recommended for teams + open source.

Resolution: `--pattern` flag wins; else non-interactive mode picks A; else interactive prompt.

### Stage 3 — create wiki dir + config

Creates `<wiki_dir>/raw/`, `<wiki_dir>/wiki/`, writes `<wiki_dir>/CLAUDE.md` from `templates/wiki_root_CLAUDE.md`, writes `<wiki_dir>/.asof.json` with the new project entry. Refuses to overwrite an existing project block (re-run with a different `project_name` to add another project).

For Pattern C, the committed `.asof.json` omits both `wiki_dir` and `source` so it travels portably across forks/clones.

### Stage 4 — scaffold project pages

Creates `wiki/<project>/` with `entities/`, `concepts/`, `sources/` subdirs. Renders the four bookkeeping templates (`wiki_index`, `wiki_log`, `wiki_candidates`, `wiki_current_state`) into the project dir with placeholders substituted (`PROJECT_NAME`, `PROJECT_SLUG`, `TODAY`, `WIKI_DIR`, `ASOF_VERSION`).

Each rendered page passes through `verify_substituted()` (no leftover `{{KEY}}` placeholders) and `verify_frontmatter_ok()` (parseable YAML fence per SCHEMA.md §3) — fail-fast if substitution missed something.

### Stage 5 — integrations

Four optional actions (each interactive yes/no, or driven by flags):

1. **Install the two-file CLAUDE.md / asof-context integration.** Writes the bulk wiki-precedence body to `<project>/.claude/asof-context.md` (sync-excluded by default since `.claude/` is in `DEFAULT_EXCLUDES`), then appends a 3-line marker-fenced `@`-import block to `<project>/CLAUDE.md` that transitively loads the bulk file via Claude Code's session-start memory loader. The two-file split prevents asof's own bootstrap content from being sync-mirrored into the wiki's `raw/` as if it were source. `asof-context.md` is written FIRST (atomicity: if it fails, CLAUDE.md is untouched, so we never end up with a CLAUDE.md importing a missing file). Marker fences make idempotent re-runs safe.
2. **Install the PostToolUse change-reminder hook** in `<project>/.claude/hooks/asof_wiki_change_reminder.py` — fires after `*.md` edits with a "wiki may now be stale" reminder. Per-project debounced + path-traversal-safe (gpt-5.2-pro round-2 fixes baked in).
3. **Edit settings file** to register the hook + (Pattern A/B) add `wiki_dir` to `permissions.additionalDirectories` + (all patterns) pre-approve `Read(wiki_dir/**)` and project-scoped `Write/Edit/MultiEdit(wiki_dir/wiki/<project_slug>/**)` in `permissions.allow` so per-file ingest doesn't trigger Claude Code's permission prompt for every source-summary write — at 50 source files × ~5 writes per ingest, the unpermitted path is unusable. Tight scope: writes are project-scoped (NOT wiki-wide, so init for project A in a shared Pattern A vault doesn't grant write access to project B's pages); `raw/` (rsync-managed) and root files (init/sync-managed) are NOT pre-approved. **Default target: `.claude/settings.local.json`** (gitignored — machine-portable absolute paths don't end up in commits). `--commit-settings` opts into the committed `.claude/settings.json`.
4. **Run a first sync** (`asof:sync --project <slug> --non-interactive`) for immediate feedback that the wiki is wired correctly.

For Pattern C, the `additionalDirectories` portion of step 3 is skipped (the wiki is already inside the repo's cwd, so no extra registration is needed for Claude Code to read it). The `permissions.allow` rules still fire for Pattern C — they're what skip the per-file ingest prompts, and that wart applies to all patterns.

## CLI flags

| Flag | Effect |
|---|---|
| `--pattern A\|B\|C` | Layout choice; skips the interactive prompt. |
| `--wiki-dir PATH` | Override default wiki dir (Pattern A: `~/.claude/asof/`; Pattern B: `~/.claude/asof-<source-name>/`). Ignored for Pattern C. |
| `--non-interactive` / `--yes` | Skip all prompts; use documented defaults. |
| `--dry-run` | Report what would happen; don't write to disk. |
| `--no-install-hook` | Skip stage-5 hook installation. |
| `--no-claudemd-snippet` | Skip stage-5 CLAUDE.md append. |
| `--no-additional-directories` | Skip BOTH the `permissions.additionalDirectories` edit AND the bundled `permissions.allow` pre-approval rules. (Without these rules, the agent will be prompted to approve every file write during ingest — at scale this makes the plugin unusable, so opt-out is rarely the right choice.) |
| `--skip-first-sync` | Don't run a first sync at the end. |
| `--commit-settings` | Write to `.claude/settings.json` (committed) instead of `.claude/settings.local.json` (gitignored). |
| `--import-existing PATH` | (v1 stub — exits 5) Migrate from a brain-sync layout. |
| `--version` | Print version and exit. |

`--non-interactive` is also auto-detected when stdin is not a TTY (CI / pipeline runs).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | User aborted at an interactive prompt |
| 2 | Preflight failure (required dependency missing) |
| 3 | Scaffold error (template substitution / config write / duplicate project) |
| 4 | Integration error (settings JSON malformed, hook copy failed) |
| 5 | `--import-existing` not yet implemented (v1 stub) |

## Common invocations

```bash
# Interactive bootstrap (most common)
/asof:init my-project /path/to/repo

# CI / scripted setup with all defaults
/asof:init my-project /path/to/repo --pattern A --non-interactive

# Pattern C (wiki inside the repo, recommended for open source / teams)
/asof:init my-project /path/to/repo --pattern C --non-interactive

# Dry run to see what would happen
/asof:init my-project /path/to/repo --pattern A --dry-run

# Minimal install (no hook, no CLAUDE.md edit, no first sync)
/asof:init my-project /path/to/repo --non-interactive \
    --no-install-hook --no-claudemd-snippet --skip-first-sync

# Commit the settings file (only when paths are project-portable)
/asof:init my-project /path/to/repo --pattern C --non-interactive \
    --commit-settings
```

## Behavioral guarantees

- **Idempotent**: re-running with a different project_name adds the new project to an existing wiki. Re-running with the same slug fails-fast (exit 3) — no silent overwrite.
- **Marker-fenced CLAUDE.md edit**: re-runs detect `<!-- asof-wiki:precedence-block -->` and skip rather than duplicate.
- **Slug validation**: `project_name` is slugified by sync's canonical `slugify()` (lowercase, `[a-z0-9-]`, max 64 chars, must start/end alphanumeric); names containing `..`, `/`, `\`, NUL, etc. are rejected upstream of any filesystem write.
- **Atomic writes**: `.asof.json` and settings files are written via temp-then-rename. A kill mid-write leaves either the old file or the new one, never a partial.
- **Dry-run leaves zero filesystem traces**: validates by mocking through every stage's I/O.
- **Post-render self-check**: every rendered template is re-parsed through `extract_frontmatter()` to catch substitution bugs before they reach the user as cryptic lint failures (gpt-5.2-pro round-1 phase-3 advice).

## Cross-references

- [PLAN.md](../../PLAN.md) section 6.1 — full wizard spec.
- [PLAN.md](../../PLAN.md) section 4 — three-pattern wiki dir resolution.
- [SCHEMA.md](../../references/SCHEMA.md) — what a conformant wiki looks like.
- [scripts/init.py](scripts/init.py) — entry point.
- [scripts/preflight.py](scripts/preflight.py) — stage 1 system checks.
- [scripts/wizard.py](scripts/wizard.py) — stage 2 + 5 interactive prompts.
- [scripts/scaffold.py](scripts/scaffold.py) — stages 3 + 4 filesystem writes.
- [scripts/integrations.py](scripts/integrations.py) — stage 5 actions.
