# asof

**A time-aware wiki for Claude Code.** Older claims age, newer ones supersede, the wiki tells you what's true *as of now*.

<!-- TODO(phase 7): replace with asciinema cast at docs/demo.cast — 30-second init → sync → supersession-in-action loop -->
> **Demo:** screenshots and a 30-second cast land with the v1.0 release. For now, see [PLAN.md](PLAN.md) for the full design or [`examples/`](examples/) for sample wikis.

## Install in one command

```bash
git clone https://github.com/<your-org>/asof ~/.claude/skills/asof
```

Then type `/asof:init` in any Claude Code session — a guided wizard handles the rest.

> **Plugin marketplace install** (`claude plugin install asof@<marketplace>`) lands when `asof` is published to a marketplace. The manual `git clone` above works on every Claude Code version today.

## What you get

- **Time-aware.** Every wiki claim is tagged with the source's mtime; newer sources supersede older ones automatically and the supersession is recorded, not silently overwritten.
- **Self-correcting.** The agent re-ingests deltas (new / modified / deleted source files) using documented rules. Stale claims get flagged and updated; deleted sources don't disappear silently — they're marked `removed_upstream:` so the history stays intact.
- **Multi-project.** One wiki dir holds N projects with hard subdirectory isolation, or one wiki per repo if you prefer (Pattern C — wiki travels with the code).

## Why this exists

Most LLM-doc setups look like RAG: upload a pile of files, retrieve relevant chunks at query time, generate an answer. The LLM rediscovers knowledge from scratch on every question. Nothing accumulates.

`asof` is different. The agent **incrementally builds and maintains a wiki** — markdown files between you and the raw sources, owned and updated by the LLM. Add a new source, the agent integrates it: updates entity pages, revises summaries, flags contradictions with the *date* of each claim. The synthesis already reflects everything you've read. Older facts age; newer ones supersede; the wiki keeps getting richer with every source.

The compounding artifact is the point. Cross-references already there. Contradictions already flagged. Synthesis already done.

## 5-minute tour

```
/asof:init <project-name> <source-path>
```
Walks you through preflight checks (Python? rsync? Obsidian?), wiki layout choice (shared / per-project / inside-repo), and integrations. Runs once per project.

```
/asof:sync <project-name>
```
Mirrors `*.md` files from your repo into the wiki's `raw/` dir. Detects what changed (NEW / MODIFIED / DELETED). The agent re-ingests the deltas with you reading the diff.

```
/asof:lint
```
Audits the wiki for stale claims, missing supersession notes, orphan pages, broken source paths. Optional `--fix` for safe repairs.

## Three layouts, pick one (or let `init` ask you)

**Pattern A — shared wiki, multiple projects.** One vault at `~/.claude/asof/` with subdirectories per project. Open in Obsidian, browse everything. Recommended for solo users with multiple related projects.

**Pattern B — one wiki per project.** Each project gets its own dir under home (e.g. `~/.claude/asof-work/`). Stricter visual isolation; one Obsidian vault per project.

**Pattern C — wiki inside the source repo.** Wiki at `<repo>/.asof/`, committed alongside code, travels with the repo via git. Recommended for teams and open-source projects. The wiki is a public artifact your collaborators get for free with `git pull`.

`init` walks you through the choice. The default is A.

## Requirements

| Component | Required? | Why |
|---|---|---|
| **Python 3.9+** | Required | All helper scripts |
| **`rsync`** | Required | Source → raw mirroring |
| **Claude Code** | Required | The skill runtime |
| **`git`** | Recommended | Versioning the wiki, especially Pattern C |
| **Obsidian** | Optional | Best UX for browsing the wiki — graph view, backlinks, frontmatter queries |

**Zero Python deps. Zero Node deps. One Python script per skill, all stdlib.** No `pip install`, no `npm i`, no `uv`. If you have Python 3.9+ and rsync, you're done.

## What happens during a sync

1. `rsync` mirrors `*.md` files from your `source` repo into `<wiki_dir>/raw/<project>/`.
2. Helper script reads existing wiki source-summaries, extracts each one's `source_mtime`, and detects deltas:
   - **NEW** — file in `raw/` but no source-summary yet
   - **MODIFIED** — source-summary's `source_mtime` ≠ current file mtime
   - **DELETED** — source-summary cites a path no longer in `raw/`
3. The agent re-ingests deltas with you. NEW files get summarized + linked into entity / concept pages. MODIFIED files get a self-supersession note. DELETED files get marked `removed_upstream:` (the source-summary stays — history is preserved).
4. Bookkeeping: `index.md`, `log.md`, and `_candidates.md` get appended.

**You read the diff and approve.** The agent doesn't write to the wiki without you watching the first time, and never silently overwrites an existing claim — supersession is always recorded.

## Why not RAG?

RAG re-derives synthesis on every query. Ten queries that all touch the same five sources = ten rounds of "find chunks, piece them together, generate answer." The synthesis is ephemeral.

`asof` does the synthesis once (when a source is ingested) and **caches it as a wiki page**. Subsequent queries read the wiki, not the raw sources. Cross-references already there. Contradictions already flagged. The wiki compounds; RAG doesn't.

## Does it work for non-code projects?

Yes. The schema is domain-agnostic. See [`examples/`](examples/):

- `codebase-wiki/` — fictional codebase being documented over time
- `research-wiki/` — research topic with papers, articles, evolving thesis
- `book-wiki/` — fan-wiki style ingest of a novel

Each example has 3-5 source `.md` files plus the resulting wiki, lint-clean.

## Codex / OpenCode compatibility

Claude Code's skill format follows the [Agent Skills open standard](https://agentskills.io) (per the [official Claude Code skills documentation](https://code.claude.com/docs/en/skills)). `asof` is intended to be compatible with any compliant tool. Verified for Claude Code; spot-checked for others per release.

## Multi-user collaboration

v1 is single-user. The wiki is plain markdown in a git repo, so async collaboration via PRs works fine. Concurrent live editing from two Claude Code sessions on the same wiki dir is **not** supported — you get a file lock collision, the second session waits.

## Migrating from `brain-sync`

If you're already running the prototype `~/.claude/skills/brain-sync/` skill with `~/Desktop/Brain/`:

```
/asof:init --import-existing ~/Desktop/Brain/
```

Reads your existing `.brain-sync.json`, copies the project list to `.asof.json`, preserves all wiki pages, writes the schema-version field. Old `brain-sync` keeps working until you're satisfied; delete it once verified.

## Production-readiness

`asof` ships with:

- Full four-cell **schema-version compatibility matrix**: refuse / read-only / require-`--migrate` / work-as-is. Never auto-migrates silently. Pre-migration backup mandatory.
- **Concurrency-safe.** `fcntl.flock` on the wiki dir for sync and lint; the change-reminder hook backs off when a sync is running.
- **Self-ingest hard guard** for Pattern C — sync refuses to recurse into its own wiki dir.
- **Input sanitization.** Project names slugified; `Path.resolve()` containment check before any write.
- **Non-interactive mode** for CI / scripted setups (`--non-interactive`, `--yes`, `--dry-run`, `ASOF_NON_INTERACTIVE=1`).
- **rsync `--safe-links`** by default to avoid path-escape via symlinks.
- **CI lints all three example wikis on every PR**; schema-version bumps require a migration script + example updates in the same PR.

See [`PLAN.md`](PLAN.md) for the full design and [`CHANGELOG.md`](CHANGELOG.md) for release notes.

## Contributing

Issues and PRs welcome. The plan is locked but extensible. See [`PLAN.md`](PLAN.md) section 17 for the schema-evolution discipline before proposing schema changes.

## License

MIT. See [`LICENSE`](LICENSE).

## Credits

Pattern from [Andrej Karpathy's LLM wiki idea](https://gist.github.com/karpathy/d4a35cffd1d23c2ddd6b3c8b1d1b76f2) (the wiki-as-compounding-artifact framing). Time-aware schema, supersession rules, three-pattern wiki dirs, and the skill packaging are this project's contribution.
