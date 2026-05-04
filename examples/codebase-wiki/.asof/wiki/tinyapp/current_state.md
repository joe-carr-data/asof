---
title: tinyapp — current state
type: overview
project: tinyapp
sources:
  - path: raw/tinyapp/README.md
    source_mtime: 2026-05-04
    ingested: 2026-05-04
  - path: raw/tinyapp/docs/architecture.md
    source_mtime: 2026-05-04
    ingested: 2026-05-04
  - path: raw/tinyapp/docs/cli.md
    source_mtime: 2026-05-04
    ingested: 2026-05-04
tags: [overview, snapshot]
last_updated: 2026-05-04
---

# tinyapp — current state

## Snapshot date

2026-05-04 — wiki bootstrapped, all three source docs ingested.

## TL;DR

`tinyapp` is a streaming CSV stats CLI. Reads UTF-8 CSV row-by-row, prints per-column mean/median/count. Streaming design (one row at a time) was a v0.2 redesign after v0.1's `pandas.read_csv()` approach OOM'd on multi-GB inputs. Median is approximated via reservoir sampling (default window 10000) — accurate to ~1%.

## Status

- **Wiki**: bootstrapped 2026-05-04, schema v1.0
- **Sources ingested**: 3 (README, docs/architecture.md, docs/cli.md)
- **Active version**: v0.2 (no pandas dependency)

## Active artifacts

- CLI: `tinyapp <file.csv> [--columns col1,col2] [--median-window N] [--no-median]`
- Default `--median-window`: 10000

## See also

- [index.md](index.md) — full catalog
- [Streaming reader](entities/streaming_reader.md)
- [Median approximation](concepts/median_approximation.md)
