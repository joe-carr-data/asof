---
title: Hoffmann 2022 — Training Compute-Optimal LLMs (Chinchilla)
type: source-summary
project: scaling-laws
sources:
  - path: raw/scaling-laws/papers/hoffmann_2022_chinchilla.md
    source_mtime: 2022-03-15
    ingested: 2026-05-04
tags: [paper, scaling-law, chinchilla, hoffmann, 2022]
last_updated: 2026-05-04
---

# Hoffmann 2022 — Chinchilla

## Claims as of source_mtime 2022-03-15

1. Most existing large models are **under-trained** — too many parameters for too few tokens.
2. Compute-optimal training scales model size and dataset size **roughly equally** (≈ 20 tokens per parameter).
3. Kaplan 2020's 73/27 compute-allocation rule used a fixed learning-rate schedule across sizes, which biased the optimum toward larger models. Chinchilla varies the LR schedule per size.

## Supersedes

Kaplan 2020's compute-allocation prescription. Previously: 73% compute → params, 27% → data. Superseded by Chinchilla: roughly 50/50 (in compute terms), or N ≈ D/20 in raw counts.

## Cross-references

- Previous view: [Kaplan 2020 summary](kaplan_2020_scaling_laws.md).
- Concept that synthesizes both: [Compute-optimal allocation](../concepts/compute_optimal_allocation.md).
