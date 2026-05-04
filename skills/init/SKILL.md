---
name: init
description: Bootstrap a time-aware asof wiki for a project. Five-stage interactive wizard — preflight checks, layout choice (shared / per-project / in-repo), wiki dir + config creation, project page scaffold, integrations (CLAUDE.md snippet, change-reminder hook, settings.json edits, optional first sync). Use when starting a wiki for a new codebase, research topic, business KB, book club, or any markdown corpus that should be remembered with mtime semantics.
when_to_use: Trigger phrases — "init the wiki", "asof init", "set up the wiki", "create a wiki for this project", "bootstrap an asof wiki", "start tracking docs over time for X", "scaffold a new asof wiki".
disable-model-invocation: true
allowed-tools: Bash(python3 *) Bash(mkdir *) Bash(cp *) Bash(test *) Bash(which *) Bash(rsync --version) Bash(git --version) Bash(stat *) Read Write Edit
argument-hint: "[project-name] [source-path] [--pattern A|B|C] [--wiki-dir PATH] [--non-interactive] [--yes] [--dry-run] [--no-install-hook] [--no-claudemd-snippet] [--no-additional-directories] [--skip-first-sync] [--commit-settings]"
---

# asof:init

Bootstrap a new asof wiki for a project. Five-stage interactive wizard backed by independent Python modules (`preflight`, `wizard`, `scaffold`, `integrations`). Designed to be deliberate (one-time per project) and idempotent (re-runs detect existing state and skip).

## How to invoke

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/init.py $ARGUMENTS
```

Both positional args (`project_name`, `source_path`) are required. Everything else is optional with documented defaults.

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
3. **Edit settings file** to register the hook + add wiki_dir to `permissions.additionalDirectories`. **Default target: `.claude/settings.local.json`** (gitignored — machine-portable absolute paths don't end up in commits). `--commit-settings` opts into the committed `.claude/settings.json`.
4. **Run a first sync** (`asof:sync --project <slug> --non-interactive`) for immediate feedback that the wiki is wired correctly.

Pattern C automatically forces the additional-directories step off (the wiki is already inside the repo).

## CLI flags

| Flag | Effect |
|---|---|
| `--pattern A\|B\|C` | Layout choice; skips the interactive prompt. |
| `--wiki-dir PATH` | Override default wiki dir (Pattern A: `~/.claude/asof/`; Pattern B: `~/.claude/asof-<source-name>/`). Ignored for Pattern C. |
| `--non-interactive` / `--yes` | Skip all prompts; use documented defaults. |
| `--dry-run` | Report what would happen; don't write to disk. |
| `--no-install-hook` | Skip stage-5 hook installation. |
| `--no-claudemd-snippet` | Skip stage-5 CLAUDE.md append. |
| `--no-additional-directories` | Skip the `permissions.additionalDirectories` edit. |
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
