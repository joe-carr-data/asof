---
title: tinyapp — wiki log
type: overview
project: tinyapp
tags: [log, audit-trail]
last_updated: 2026-05-04
---

# tinyapp — wiki log

Append-only chronological log of ingests, queries, and lint passes for this
project. Greppable with:

```bash
grep "^## \[" log.md
grep "^## \[" log.md | tail -5            # last 5 entries
grep "^## \[2026" log.md                  # all 2026 entries
```

Format (per SCHEMA.md §8 — see `<wiki_dir>/CLAUDE.md` for the plugin path that hosts the full spec):

```
## [YYYY-MM-DD] action | source-or-page | mtime=YYYY-MM-DD
- one-line summary
- pages touched: comma-separated relative paths
```

Allowed actions: `scaffold`, `ingest`, `sync`, `self-supersession`,
`removed-upstream`, `candidate-promoted`, `query`, `lint`, `mtime-correction`,
`tooling-fix`, `ingest-aborted`. See SCHEMA.md §8 for when to use each.

---

## [2026-05-04] scaffold | wiki/tinyapp/ initialized
- Folders created: entities/, concepts/, sources/
- Seed files: index.md, _candidates.md, current_state.md, log.md
- Bootstrapped from asof v0.1.0-dev, schema v1.0

(`scaffold` action documented in SCHEMA.md §8.)
