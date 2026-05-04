---
title: Streaming reader
type: entity
project: tinyapp
sources:
  - path: raw/tinyapp/docs/architecture.md
    source_mtime: 2026-05-04
    ingested: 2026-05-04
tags: [reader, streaming, csv]
aliases: [reader, csv-reader]
last_updated: 2026-05-04
---

# Streaming reader

The CSV reader in `reader.py`. Yields one row at a time so memory stays O(columns) regardless of input size.

## Why it exists

The v0.1 prototype loaded the full file with `pandas.read_csv()`, which OOM'd on the multi-GB inputs the user's actual workload produced. v0.2 replaced it with a streaming reader that uses only stdlib `csv` — pandas was dropped as a dependency.

## Interface

- Constructor: `Reader(path: Path)` — opens the file, parses the header.
- Iterator: yields a `dict[str, str]` per row (column name → cell text).
- `header: tuple[str, ...]` — the parsed column names.

## Cross-references

- Source: [docs/architecture.md summary](../sources/architecture.md).
- Median approximation depends on this reader's streaming guarantee → [Median approximation](../concepts/median_approximation.md).
