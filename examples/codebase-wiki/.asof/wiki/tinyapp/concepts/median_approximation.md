---
title: Median approximation
type: concept
project: tinyapp
sources:
  - path: raw/tinyapp/docs/architecture.md
    source_mtime: 2026-05-04
    ingested: 2026-05-04
tags: [stats, approximation, reservoir-sampling]
last_updated: 2026-05-04
---

# Median approximation

Why tinyapp doesn't compute the true median: true median requires sorting the entire column, which requires the full dataset in memory. That defeats the streaming-reader design's whole point.

## How tinyapp approximates

Each column maintains a **reservoir** of size `--median-window` (default 10000). As rows stream past, the reservoir is updated via classical reservoir sampling so the sample is uniform over rows seen so far. The reported median is the median of the reservoir.

## Accuracy

Per architecture.md, the approximation is accurate to ~1% on the test corpus. For workloads requiring exact medians, users can pipe the column through an external sort utility (`sort -n | uniq -c` or similar) — out of scope for tinyapp.

## Cross-references

- Implementation lives in the [Streaming reader](../entities/streaming_reader.md)'s consumers.
- Configurable via `--median-window` ([CLI summary](../sources/cli.md)).
- Source: [docs/architecture.md summary](../sources/architecture.md).
