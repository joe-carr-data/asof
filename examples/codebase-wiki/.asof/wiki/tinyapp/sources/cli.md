---
title: docs/cli.md — CLI surface
type: source-summary
project: tinyapp
sources:
  - path: raw/tinyapp/docs/cli.md
    source_mtime: 2026-05-04
    ingested: 2026-05-04
tags: [cli, interface, exit-codes]
last_updated: 2026-05-04
---

# docs/cli.md — CLI surface

## Claims as of source_mtime 2026-05-04

- CLI shape: `tinyapp <file.csv> [--columns col1,col2] [--median-window N] [--no-median]`
- `<file.csv>` is required positional; UTF-8, comma-delimited, header row required.
- `--columns` defaults to "all numeric columns".
- `--median-window` default is 10000.
- `--no-median` skips median computation (faster on wide files).
- Exit codes: 0 (success), 1 (file not found), 2 (header malformed), 3 (no numeric columns).

## Cross-references

- Why `--median-window` exists: [Median approximation](../concepts/median_approximation.md).
- Project overview: [README summary](README.md).
