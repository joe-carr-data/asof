# asof

**Claude writes notes that read as "now". `asof` tags them with mtimes so older claims stop pretending to be current.**

A Claude Code plugin that gives the agent a sense of time across your project's docs. Source markdown files get rsync-mirrored into a wiki; an LLM-maintained synthesis under `wiki/` records *when* each claim was true, with explicit supersession when newer sources contradict older ones.

## Install

In any Claude Code session:

```
/plugin marketplace add joe-carr-data/asof
/plugin install asof@asof
```

That's it. The plugin auto-discovers from `.claude-plugin/plugin.json` at the repo root — no separate marketplace registration needed. Skills land namespaced under `asof:`, so:

```
/asof:init <project-name> <path-to-source-repo>
```

A 5-stage wizard handles preflight checks (Python 3.9+, rsync), wiki layout choice, scaffolding, and integrations (CLAUDE.md snippet, change-reminder hook, settings file, optional first sync).

**Zero runtime dependencies beyond Python stdlib + rsync.** No `pip install`, no `npm i`, no `uv sync`.

### Local development install

If you've cloned the repo and want to test changes against your own checkout:

```bash
claude --plugin-dir /path/to/asof
```

Reload after code changes with `/reload-plugins` — no Claude Code restart needed.

For the full plugin install / publish reference, see [Claude Code's plugin docs](https://code.claude.com/docs/en/plugins.md).

## Why this exists

Claude-written notes always sound final at writing time. A page edited last March may state "we use Postgres 14" with the same authority as a page edited yesterday — but only one of them reflects current reality. The agent has no built-in sense of which is which.

`asof` fixes that by recording, in every wiki page's frontmatter:

- the source file's `source_mtime` (when the underlying doc was last touched)
- when the wiki page itself was `last_updated`
- which sources, with which mtimes, this page's claims are based on

When a newer source contradicts an older one, the wiki records an explicit **supersession note** (e.g. "previously X — superseded by Y per source_mtime 2026-04-22"). Old claims aren't deleted; they're tagged stale, with the date attached. The agent reads the wiki on every project question and trusts the freshest claim.

## The three skills

```
/asof:init <project> <source>     # one-time bootstrap, 5-stage wizard
/asof:sync [project]              # mirror sources into raw/, detect deltas (NEW/MODIFIED/DELETED)
/asof:lint [project] [--fix]      # audit: stale claims, missing supersessions, orphan pages, broken paths
```

`init` runs once per project. `sync` runs whenever your source docs change. `lint` runs on demand or in CI.

## Three layouts (`init` asks, defaults to A)

| Pattern | Wiki location | Best for |
|---|---|---|
| **A** | `~/.claude/asof/` (shared) | Solo users with multiple projects |
| **B** | `~/.claude/asof-<project>/` (per-project under home) | Strict project isolation |
| **C** | `<repo>/.asof/` (in the source repo) | Teams + open source — wiki travels with the code via git |

## Examples

Three Pattern C example wikis live under [`examples/`](examples/):

- [`examples/codebase-wiki/`](examples/codebase-wiki/) — `tinyapp` CLI demo. Source-summary + entity + concept cross-references.
- [`examples/research-wiki/`](examples/research-wiki/) — Kaplan 2020 → Hoffmann 2022 (Chinchilla) scaling-law papers. **Demonstrates cross-source supersession** with a 26-month mtime gap.
- [`examples/book-wiki/`](examples/book-wiki/) — Kahneman, *Thinking, Fast and Slow*. Aliased entities (System 1, System 2) + concept (Cognitive ease).

CI lints all three on every push (`.github/workflows/lint-examples.yml`), so they stay portable across forks and clones.

## Pattern C and `.asof/raw/`

If you copy an example as a template, **read this first.** Pattern C wikis (`<repo>/.asof/`) treat `raw/` as regenerable working state — `init` writes a `.gitignore` block excluding `.asof/raw/`, `.asof/.last-sync/`, and the lock file. `/asof:sync` recreates `raw/` from your sources whenever needed.

The **shipped examples do the opposite**: each example's `.gitignore` has the `.asof/raw/` line commented out (with an inline note pointing to it), and `raw/` is committed. This is deliberate — CI and a fresh-checkout `/asof:lint` need the raw files to validate `sources[].path` references.

When you derive your own wiki from an example, uncomment the `.asof/raw/` line in `.gitignore` immediately. Otherwise you'll commit a regenerable working tree to your repo. The line is fenced inside the `# asof-wiki:gitignore` markers, so the diff is one line.

## Requirements

| Component | Required? | Why |
|---|---|---|
| Python 3.9+ | Required | All helper scripts (stdlib only) |
| `rsync` | Required | Source → raw mirroring (used by `asof:sync`) |
| Claude Code | Required | The skill runtime |
| `git` | Recommended | Versioning the wiki, especially Pattern C |
| Obsidian | Optional | Best UX for browsing — graph view, backlinks, frontmatter queries |

## How sync works

1. `rsync` mirrors `*.md` files from `<source>` into `<wiki_dir>/raw/<project>/`.
2. The skill compares each raw file's mtime against any existing source-summary's `source_mtime` and classifies deltas:
   - **NEW** — file in `raw/`, no source-summary yet → agent writes one
   - **MODIFIED** — `source_mtime` changed → agent updates the summary, adds a self-supersession note
   - **DELETED** — source-summary cites a path no longer in `raw/` → agent marks it `removed_upstream:`, page survives as historical record
3. The agent presents the diff. You approve. Bookkeeping (`index.md`, `log.md`, `_candidates.md`) updates.

The agent never silently overwrites a claim. Supersessions are explicit. `fcntl.flock` on `<wiki_dir>/.asof.lock` prevents concurrent corruption.

## What lint checks

7 checks, exit code maps cleanly for CI:

| Check | Severity | Detects |
|---|---|---|
| frontmatter | ERROR | Missing required fields, unparseable ISO dates, source-summary contract violations |
| path-mismatch | ERROR | `sources[].path` doesn't exist (or escapes the project's `raw/`) |
| missing-mtime | ERROR | Source entries lacking `source_mtime` |
| removed-source | WARN | Pages with `<!-- backing source removed -->` markers |
| mtime-drift | WARN | Page `last_updated` more than 30 days behind its newest source |
| supersession-gap | WARN | Page cites sources spanning 60+ days with no supersession note |
| orphan-page | INFO | Pages with no inbound link from `index.md` or other pages |

Two narrow `--fix` cases: insert today's date when `last_updated` is *missing entirely* (refuses to overwrite stale-but-present), and append orphan-page entries to `index.md`. Everything else is report-only.

For CI: pin `--severity warn` so orphan-page INFO findings don't gate merges.

## Schema-version compatibility

Two version axes: the **skill version** in `.claude-plugin/plugin.json` (tracks the code, what `git tag` points at) and the **schema version** in `<wiki_dir>/.asof.json` (tracks the wiki format). Each wiki also pins `min_reader_version` and `min_writer_version` so a wiki written by a newer skill can still be read by an older one within the supported window.

| Skill version vs wiki floors | `sync` / `lint --fix` / `migrate` | `lint` (read-only) |
|---|---|---|
| `< min_reader_version` | refuse, upgrade required | refuse |
| `min_reader ≤ skill < min_writer` | refuse with read-only message | allowed |
| `≥ min_writer`, schema match | allowed | allowed |
| `≥ min_writer`, wiki schema older | require explicit `--migrate` (with backup) | allowed |

Migrations are never silent. See [`CHANGELOG.md`](CHANGELOG.md) for the full semantics and migration procedure.

## Multi-user

v1 is single-user. The wiki is plain markdown in a git repo, so async PR-style collaboration works fine. Concurrent live editing from two Claude Code sessions on the same wiki dir is **not** supported — `fcntl.flock` makes the second session wait, no corruption.

## Production-readiness

- Self-ingest hard guard for Pattern C (sync refuses to recurse into its own wiki dir).
- `Path.resolve()` containment checks before every write.
- rsync `--safe-links` by default (path-traversal via symlinks blocked).
- Atomic writes (temp-then-rename) for every config + markdown + JSON write.
- Non-interactive mode for CI (`--non-interactive`, `--yes`, `--dry-run`, `ASOF_NON_INTERACTIVE=1`).
- Three example wikis lint clean from a fresh checkout on every push.
- 595 unit + integration tests; init/sync/lint exercised as real subprocesses, with lock-contention coverage.

## Documentation

- [`PLAN.md`](PLAN.md) — full design (skill specs, schema, hooks, distribution).
- [`references/SCHEMA.md`](references/SCHEMA.md) — wiki format spec (frontmatter, page types, time-aware ingest rules).
- [`skills/init/SKILL.md`](skills/init/SKILL.md), [`skills/sync/SKILL.md`](skills/sync/SKILL.md), [`skills/lint/SKILL.md`](skills/lint/SKILL.md) — per-skill docs.
- [`CHANGELOG.md`](CHANGELOG.md) — release notes + schema-version semantics.

## License

MIT. See [`LICENSE`](LICENSE).

## Credits

The wiki-as-compounding-artifact framing comes from [Andrej Karpathy's gist on a personal LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Time-aware schema, supersession rules, the three wiki-dir patterns, and the skill packaging are this project's contribution.
