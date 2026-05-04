---
title: README — tinyapp project overview
type: source-summary
project: tinyapp
sources:
  - path: raw/tinyapp/README.md
    source_mtime: 2026-05-04
    ingested: 2026-05-04
tags: [overview, layout]
last_updated: 2026-05-04
---

# README — tinyapp project overview

## Claims as of source_mtime 2026-05-04

- `tinyapp` is a CLI that reads CSV and prints per-column summary statistics (mean, median, count).
- Project layout: top-level `README.md`, `docs/` for design + CLI surface, `.asof/` for the in-repo wiki (Pattern C).
- The README explicitly frames the project as a stand-in for a real codebase — its purpose in this wiki is to demo the asof workflow, not to ship a useful tool.

## Cross-references

- Architecture details: see [Streaming reader](../entities/streaming_reader.md).
- CLI surface: see [docs/cli.md summary](cli.md).
