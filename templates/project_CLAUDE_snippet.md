<!--
This snippet is appended (not overwritten) to a user's project CLAUDE.md
by `asof:init`. It establishes wiki-precedence — telling future Claude Code
sessions in this project to consult the asof wiki BEFORE auto-memory or
individual docs for synthesised claims.

Placeholders {{WIKI_DIR}}, {{PROJECT_SLUG}}, {{PROJECT_NAME}} are substituted
at install time. The leading marker `<!-- asof-wiki:precedence-block -->` lets
init detect re-runs and refuse to duplicate the section.
-->

<!-- asof-wiki:precedence-block -->

## Project Knowledge Wiki ({{PROJECT_NAME}})

**Before answering any question about project state, decisions, topology, history, or "what is X / how does X work", FIRST read `{{WIKI_DIR}}/wiki/{{PROJECT_SLUG}}/current_state.md` and `{{WIKI_DIR}}/wiki/{{PROJECT_SLUG}}/index.md`.** This is not optional — the wiki is the canonical, time-aware knowledge base for this project, and it **supersedes both auto-memory and individual `*.md` docs** for synthesised claims. Auto-memory may contain stale facts that the wiki has already superseded.

A living wiki built from this repo's `*.md` docs lives at `{{WIKI_DIR}}/wiki/{{PROJECT_SLUG}}/`. It cross-references and supersedes claims across the whole documentation collection — `docs/`, `research/`, root-level plans, status files — and applies time-aware conflict resolution that grepping individual docs cannot.

**Reading order when answering a question:**
1. `current_state.md` — latest synthesised project snapshot (start here)
2. `index.md` — one-line catalog of every page (entities, concepts, sources)
3. Drill into specific entity / concept pages from the index
4. Only after the wiki: cross-check with auto-memory or raw docs if needed

**Precedence rule:** wiki claim > auto-memory claim > individual doc claim. If the wiki contradicts auto-memory, trust the wiki and update the memory entry to point at the wiki.

**Schema (important for accuracy):**
- Every page records `source_mtime` per cited source in YAML frontmatter. **Newer mtimes supersede older ones.**
- Look for explicit "superseded" / "Previously X — superseded by Y" notes. Old claims are kept for history; do not quote them as current truth.
- `_candidates.md` lists half-formed concepts (mention count <3). These are not yet authoritative — flag them as candidates if you cite them.
- `last_updated` on each page reflects when the wiki page itself was last revised, not the underlying source dates.

**Folder layout under `wiki/{{PROJECT_SLUG}}/`:**
- `entities/` — things in the system (people, projects, components, products)
- `concepts/` — ideas / rules / contracts / methodologies
- `sources/` — per-document summaries (one per `*.md` source from this repo)
- `log.md` — chronological audit trail of every ingest / modification / supersession

**Access from this project's Claude Code session:**

The wiki lives outside this project directory (Pattern A or B), so launch Claude Code with:

```bash
claude --add-dir {{WIKI_DIR}}
```

…or add `{{WIKI_DIR}}` to `.claude/settings.json` under `additionalDirectories` so it loads automatically every session. (`asof:init` may have done this for you already — check `.claude/settings.json`.)

**Refreshing the wiki after doc changes here:**

- Say "sync the wiki" or run `/asof:sync {{PROJECT_SLUG}}` in any Claude Code session.
- Or directly: `python3 ~/.claude/skills/asof/skills/sync/scripts/sync.py {{PROJECT_SLUG}}` (path varies by install location).
- The sync detects new / modified / deleted `*.md` files via mtime comparison and re-ingests deltas only — newer mtimes supersede older versions of the same file with explicit "Self-supersession" notes.

<!-- /asof-wiki:precedence-block -->
