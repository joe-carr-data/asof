---
title: docs/architecture.md — three-layer design
type: source-summary
project: tinyapp
sources:
  - path: raw/tinyapp/docs/architecture.md
    source_mtime: 2026-05-04
    ingested: 2026-05-04
tags: [architecture, design, streaming]
last_updated: 2026-05-04
---

# docs/architecture.md — three-layer design

## Claims as of source_mtime 2026-05-04

- Three-layer design: **CLI parser** (`cli.py`), **Reader** (`reader.py`), **Stats engine** (`stats.py`).
- The reader is **streaming**, not in-memory — memory is O(columns), not O(rows). This was a v0.2 redesign motivated by OOM on multi-GB inputs that the original v0.1 prototype hit when it used `pandas.read_csv()`.
- pandas was a dependency in v0.1; v0.2 dropped it.
- Stats engine uses **Welford's algorithm** for running mean/variance and **reservoir sampling** (size `--median-window`, default 10000) for median approximation. The reservoir-based median is accurate to ~1% on the test corpus.

## Cross-references

- Streaming-reader entity: [Streaming reader](../entities/streaming_reader.md).
- Median approximation concept: [Median approximation](../concepts/median_approximation.md).
- CLI surface: [docs/cli.md summary](cli.md).
