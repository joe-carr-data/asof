# Karpathy's LLM-wiki pattern (and what `asof` adds)

`asof` is built on top of Andrej Karpathy's "LLM wiki" pattern. This file
attributes the original idea, summarizes the core insight, and describes
what `asof` contributes on top.

## The original idea (Karpathy's framing)

Most LLM-and-document setups look like RAG: upload a pile of files, retrieve
relevant chunks at query time, generate an answer. The LLM rediscovers
knowledge from scratch on every question.

Karpathy's alternative: have the LLM **incrementally build and maintain a
wiki** — a directory of markdown files between you and the raw sources, owned
and updated by the LLM. Add a new source, the LLM doesn't just index it for
later retrieval. It reads it, extracts the key information, and integrates it
into the existing wiki — updating entity pages, revising topic summaries,
flagging contradictions, strengthening or challenging the evolving synthesis.

The compounding artifact is the point. Cross-references already there.
Contradictions already flagged. Synthesis already done. The wiki gets richer
with every source you add — you never (or rarely) write the wiki yourself.

The pattern's three layers, lightly paraphrased from Karpathy's prose:

1. **Raw sources** — your curated collection of source documents. Immutable.
   The LLM reads from them but never modifies them.
2. **The wiki** — a directory of LLM-generated markdown files: source
   summaries, entity pages, concept pages, comparisons, an overview, a
   synthesis. The LLM owns this layer entirely.
3. **The schema** — a configuration file (CLAUDE.md, AGENTS.md, etc.) that
   tells the LLM how the wiki is structured, what conventions to follow, and
   what workflows to apply when ingesting sources or answering questions.

Three operations sit on top: **ingest** (file a new source into the wiki),
**query** (answer a question against the wiki, optionally filing the answer
back as a new page), and **lint** (periodically health-check for stale
claims, missing cross-references, orphan pages).

The key human / LLM split: **the human curates sources, asks good questions,
and decides what matters.** The LLM does the bookkeeping — summarizing,
cross-referencing, filing, supersession-tracking — that makes a knowledge
base actually useful over time.

## Why the pattern works

Karpathy's observation: the tedious part of maintaining a knowledge base is
not the reading or the thinking. It's the bookkeeping. Updating
cross-references. Keeping summaries current. Noting when new data
contradicts old claims. Maintaining consistency across dozens of pages.

Humans abandon wikis because the maintenance burden grows faster than the
value. LLMs don't get bored, don't forget to update a cross-reference, and
can touch 15 files in one pass. The wiki stays maintained because the cost
of maintenance is near zero.

## What `asof` adds on top

Karpathy's pattern is a recipe, not a system. He explicitly notes the
document is "intentionally abstract" — it describes the idea, not a specific
implementation. The exact directory structure, schema conventions, page
formats, tooling — those depend on your domain, your preferences, your LLM
of choice.

`asof` is one such concrete instantiation. Specifically, it adds:

### 1. A time-aware schema

Every wiki claim is tagged with the **`source_mtime`** of the document it
cites. Newer sources supersede older ones systematically — with **explicit
supersession notes** that preserve history rather than overwrite it. The
wiki always knows what's true *as of now* without losing the trail of how
the project got there.

This is `asof`'s namesake: borrowed from temporal-database `AS OF` semantics
(Postgres, Snowflake, Datomic). Every wiki claim is implicitly "true as of
mtime X."

See [SCHEMA.md](SCHEMA.md) §6 for the full ingest rules.

### 2. Three layout patterns

Karpathy's pattern assumes a single shared brain dir. `asof` supports three:

- **Pattern A**: shared wiki, multiple projects (the original).
- **Pattern B**: per-project wiki under home (vault-per-project for stricter isolation).
- **Pattern C**: wiki inside the source repo (`<repo>/.asof/`), committed alongside code, travels with the repo via git. Best for teams and open source.

See [PLAN.md](../PLAN.md) section 4.

### 3. Three skills + an opt-in hook

Wrapped as a Claude Code plugin so the user doesn't have to wire anything by
hand:

- **`asof:init`** — interactive five-stage wizard (preflight, layout choice,
  bootstrap, scaffold, integrations).
- **`asof:sync`** — rsync source `*.md` into the wiki's `raw/` mirror;
  detect deltas (NEW / MODIFIED / DELETED) for the agent to ingest.
- **`asof:lint`** — audit the wiki for the seven schema-violation classes.
- **PostToolUse change-reminder hook** — opt-in nudge when source `*.md`
  files change in the user's project, so the wiki doesn't drift silently.

### 4. A schema-version compatibility matrix

Three version numbers travel with every wiki: `schema_version`,
`min_reader_version`, `min_writer_version`. The four-cell matrix
(refuse / read-only / require-`--migrate` / proceed) prevents silent
corruption when skill and wiki versions diverge. See [SCHEMA.md](SCHEMA.md)
§12 (the `.last-sync` JSON shape now lives at §11).

### 5. Production rigor

- `fcntl.flock` concurrency on the wiki dir; the hook backs off when held.
- Atomic writes for `.last-sync/<project>.json` (per-project, not global).
- Path-traversal guards via `Path.resolve()` containment.
- Project-name slugification.
- Self-ingest hard guard for Pattern C (`.asof` mandatory in excludes).
- Stdlib-only Python 3.9+ (no `pip install` step).
- CI lints all shipped example wikis on every PR.

### 6. Schema-evolution discipline

Codified in [PLAN.md](../PLAN.md) section 17 + enforced by CI:

- Additive-only between minor versions.
- Migration scripts ship in the same PR as breaking changes.
- The same PR updates the three shipped example wikis.
- `min_reader_version` enforces forward-incompat with a clear error.
- Pre-migration `wiki/` → `wiki.bak.<timestamp>/` backup is mandatory.

This is what justifies shipping multiple example wikis — they're not
load-bearing on every release, only on major-version bumps where migration
scripts handle them in the same PR.

## Karpathy's original document

The full pattern lives at the top of `~/Desktop/Brain/CLAUDE.md` in the
author's personal wiki. The "abstract pattern" framing intentionally leaves
the implementation up to each user's domain. `asof` is one implementation
among many that's possible from his recipe.

## Credits

- Original pattern and prose: **Andrej Karpathy.**
- `asof`'s time-aware schema, supersession rules, three-pattern wiki dirs,
  skill packaging, schema-version compatibility matrix, and production
  rigor: this project's contribution.
- Inspired indirectly by Vannevar Bush's Memex (1945) — a personal,
  curated knowledge store with associative trails between documents. The
  part Bush couldn't solve was who does the maintenance. The LLM handles
  that.
