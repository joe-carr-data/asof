---
title: scaling_laws — candidate concepts
type: overview
project: scaling-laws
tags: [candidates, deferred-promotions]
last_updated: 2026-05-04
---

# scaling_laws — candidate concepts

Half-formed concepts the wiki has noticed but not yet promoted to first-class
concept pages. The agent files candidates here when a concept is mentioned
once or twice; at the **3-mention threshold** (configurable per-wiki via
`lint_thresholds.candidate_promotion_threshold`), the candidate is promoted
to `concepts/<slug>.md`.

When you (the agent) add or update a candidate, also append a `log.md` entry
so the audit trail records the count change.

## Format

For each candidate:

```markdown
## <candidate-name>
- mention count: N
- first seen: YYYY-MM-DD (in `sources/<rel>.md`)
- most recent mention: YYYY-MM-DD (in `sources/<rel>.md`)
- summary: <one-line description>
```

When a candidate hits 3 mentions:

1. Create `concepts/<slug>.md` with proper frontmatter (per SCHEMA.md §3),
   citing all three sources that mentioned it.
2. Update `index.md` with the new concept entry.
3. Remove the candidate's section from this file.
4. Append a `## [<date>] candidate-promoted | <slug>` entry to `log.md`.

---

*(no candidates yet — added as the agent ingests sources)*
