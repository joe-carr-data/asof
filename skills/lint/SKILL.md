---
name: lint
description: Audit the asof wiki for schema violations — stale claims, missing supersession notes, orphan pages, broken source paths, frontmatter errors. Use when the user says "lint the wiki", "audit the wiki", "asof lint", "check wiki health", or after a large ingest session. Two narrow auto-fixes available via `--fix`.
when_to_use: Trigger phrases — "lint the wiki", "asof lint", "audit the wiki", "check wiki health", "find stale pages", "what's broken in the wiki", "fix the wiki", "auto-repair index entries".
disable-model-invocation: true
allowed-tools: Bash(python3 *) Bash(grep *) Bash(find *) Read Write Edit
argument-hint: "[project-name (optional)] [--fix] [--json] [--severity error|warn|info] [--dry-run]"
---

# asof:lint

Audit the wiki for schema violations and time-aware drift. Pre-flights `.asof.json` validity (refuses to run on an invalid config), then runs 7 page-level checks per project. Default output is human-readable text; `--json` is for CI pipelines.

## How to invoke

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/lint.py $ARGUMENTS
```

Both positional arg (`project_name`) and every flag are optional. Default behavior: lint every project in the resolved wiki, report at INFO severity and above.

## Pre-flight: config validity (gate, not a finding)

Before any page-level checks run, lint loads `.asof.json` via the same `load_wiki_config()` that sync and init use. If the config is invalid (malformed JSON, missing version fields, missing mandatory excludes, etc.), lint **halts immediately** with exit code 4 and the underlying `ConfigError` message.

Lint never cascades into page-level findings on top of an untrusted config — fix the config first, then re-run.

## The 7 checks

| # | Check | Severity | What it detects |
|---|---|---|---|
| 1 | **frontmatter** | ERROR | Missing required SCHEMA fields (`title`, `type`, `project`, `last_updated`); source-summary pages without `sources`; unparseable ISO dates. |
| 2 | **path-mismatch** | ERROR | Source-summaries whose `sources[].path` doesn't exist under `raw/`. Skipped for `removed_upstream:` pages (intentional historical record). |
| 3 | **missing-mtime** | ERROR | Source entries lacking `source_mtime` (data quality bug). |
| 4 | **removed-source** | WARN | Pages with `<!-- backing source removed -->` markers — flagged for review of dependent pages. |
| 5 | **mtime-drift** | WARN | `last_updated` more than `lint_thresholds.mtime_drift_days` (default 30) older than newest cited `source_mtime`. |
| 6 | **supersession-gap** | WARN | Pages citing two sources whose `source_mtime` differ by ≥ `lint_thresholds.supersession_gap_days` (default 60) without a supersession note in body. |
| 7 | **orphan-page** | INFO | Pages with no inbound link from `index.md` or other wiki pages. Bookkeeping pages (`index.md`, `log.md`, `_candidates.md`, `current_state.md`) are exempt. |

## `--fix` boundaries (extremely narrow)

Only **2** of the 7 checks are auto-fixable, and only in unambiguous variants:

| # | Check | `--fix` action | Refused when |
|---|---|---|---|
| 1 | frontmatter | When `last_updated` is **missing entirely** (not just stale), insert today's date before the closing `---` fence. | `last_updated` exists with any value; another required field is missing. |
| 7 | orphan-page | Append `- [Title](relative-path.md)` to `index.md` under the matching `## <type-section>` (Entities / Concepts / Source summaries / Comparisons / Overviews / Syntheses). | Page lacks parseable `title` / `type`; matching section doesn't exist; page already linked. |

All other checks are **report-only**. `--fix` does NOT:
- Edit page bodies (semantic decisions belong to the agent).
- Rewrite stale-but-present `last_updated` (would lie about edit recency).
- Add supersession notes.
- Delete or rename pages.
- Touch `current_state.md`, `log.md`, or `_candidates.md`.

All `--fix` writes are atomic via `atomic_write_text` (temp-then-rename), and lint holds `<wiki_dir>/.asof.lock` (same lock as sync) for the duration.

## Read-only mode (compat-matrix cell b)

When the skill version is in the read-only window (`min_reader_version ≤ skill_version < min_writer_version`), `--fix` is **rejected** with exit 4 and a "read-only mode — upgrade asof to ≥ `<min_writer_version>`" message. The lint report itself runs unaffected.

## Output format

**Default text:**

```
asof:lint /path/to/wiki  (project: myproj)

ERRORS (3):
  wiki/myproj/sources/foo.md:1   path-mismatch    raw/myproj/foo.md does not exist
  wiki/myproj/sources/bar.md:5   missing-mtime    sources[0] has no source_mtime
  wiki/myproj/entities/x.md:1    frontmatter      missing required field 'title'

WARNINGS (2):
  wiki/myproj/concepts/y.md      mtime-drift      last_updated 2026-01-10, newest source_mtime 2026-04-22 (102d drift > 30d)
  wiki/myproj/sources/z.md       removed-source   <!-- backing source removed --> marker present

INFO (1):
  wiki/myproj/concepts/orphan.md orphan-page      no inbound links from index.md or other pages

Summary: 3 errors, 2 warnings, 1 info across 47 pages.
```

**`--json`:**

```json
{
  "wiki_dir": "/path/to/wiki",
  "skill_version": "1.0.0",
  "projects": [{
    "name": "myproj",
    "page_count": 47,
    "project_level_error": null,
    "findings": [
      {"severity": "ERROR", "check": "path-mismatch", "page": "wiki/myproj/sources/foo.md", "line": 1, "message": "..."}
    ]
  }],
  "summary": {"errors": 3, "warnings": 2, "info": 1}
}
```

## CLI flags

| Flag | Effect |
|---|---|
| `[project-name]` | Lint only the named project (positional). Omit to lint every configured project. |
| `--wiki-dir PATH` | Override wiki dir resolution (default: walk-up from cwd to find `.asof/`, fall back to `~/.claude/asof/`). |
| `--fix` | Apply the 2 narrow auto-fixes. Rejected in read-only mode. |
| `--json` | Emit machine-readable JSON instead of text. |
| `--severity LEVEL` | Filter to `error`, `warn`, or `info` (default: `info` = report everything). |
| `--dry-run` | With `--fix`, report what would be fixed without writing. |
| `--non-interactive` | No prompts (auto-detected from non-TTY stdin). |
| `--version` | Print version and exit. |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean — no findings at or above the configured severity. |
| 1 | Findings present (filtered to the configured severity). |
| 2 | Internal error (template / I/O / lock acquisition failure). |
| 3 | `--fix` requested but at least one fixable finding was refused. |
| 4 | Pre-flight failure: invalid config, unknown project, or `--fix` refused due to read-only mode. |

## Common invocations

```bash
# Lint every configured project, default INFO severity (everything reported)
/asof:lint

# Lint a specific project
/asof:lint myproject

# CI mode: JSON output, only WARN+ severity (RECOMMENDED for CI gates)
/asof:lint --json --severity warn

# Apply the narrow auto-fixes (insert missing last_updated, link orphans into index.md)
/asof:lint --fix

# Preview --fix without writing
/asof:lint --fix --dry-run

# Override wiki dir (Pattern A users running from outside cwd-resolution)
/asof:lint --wiki-dir ~/.claude/asof
```

## CI integration note

The default severity threshold is `info`, which means **orphan-page findings cause exit 1**. For CI gating, prefer `--severity warn` so that orphan pages don't block merges; the agent can still address them out-of-band. Pin the threshold explicitly so a future addition of a new INFO-severity check doesn't silently fail pipelines:

```bash
asof:lint --severity warn   # exit 1 only on ERROR or WARN
asof:lint --severity error  # exit 1 only on ERROR (most permissive gate)
```

## Cross-references

- [PLAN.md](../../PLAN.md) §6.3 — full design contract.
- [SCHEMA.md](../../references/SCHEMA.md) §3 — frontmatter field requirements.
- [SCHEMA.md](../../references/SCHEMA.md) §6 — time-aware ingest rules (mtime, supersession, removal).
- [scripts/lint.py](scripts/lint.py) — entry point.
- [scripts/checks.py](scripts/checks.py) — the 7 check implementations.
- [scripts/fix.py](scripts/fix.py) — narrow `--fix` path.
- [scripts/render.py](scripts/render.py) — text + JSON output.
- [scripts/frontmatter.py](scripts/frontmatter.py) — stdlib-only YAML-shape parser.
