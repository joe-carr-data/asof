---
title: {{PROJECT_NAME}} — current state
type: overview
project: {{PROJECT_SLUG}}
sources: []
tags: [overview, snapshot]
last_updated: {{TODAY}}
---

# {{PROJECT_NAME}} — current state

> The headline synthesis for this project. Always reflects the **latest**
> claims (per [SCHEMA.md §6](../../references/SCHEMA.md#6-time-aware-ingest-rules)
> Rule 3). Historical claims contribute via supersession notes, never by
> polluting the current state.

## Snapshot date

{{TODAY}} — wiki bootstrapped. No sources ingested yet.

## TL;DR

*(filled in by the agent as sources are ingested)*

## Status

- **Wiki**: bootstrapped on {{TODAY}}, schema v1.0
- **Configured projects**: see `<wiki_dir>/.asof.json`
- **Last sync**: never — run `/asof:sync {{PROJECT_SLUG}}` to begin

## What this page should contain

Once the agent has ingested sources, this page typically holds:

- A **TL;DR** paragraph capturing the project's state in 3-5 sentences.
- A **status table** (active / paused / shipped / blocked) for the
  project's main workstreams.
- **Active artifacts** (deployed model, release version, current branch).
- **Open issues** with one-line summaries.
- **Cross-references** to entity / concept pages for deeper detail.

This page should be **scannable in 30 seconds**. Dive deeper via the
`index.md` catalog or by following entity / concept links.

## See also

- [index.md](index.md) — full catalog of pages in this project
- [_candidates.md](_candidates.md) — deferred concepts (not yet promoted)
- [log.md](log.md) — chronological audit trail
