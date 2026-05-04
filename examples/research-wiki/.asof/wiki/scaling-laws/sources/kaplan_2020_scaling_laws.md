---
title: Kaplan 2020 — Scaling Laws for Neural Language Models
type: source-summary
project: scaling-laws
sources:
  - path: raw/scaling-laws/papers/kaplan_2020_scaling_laws.md
    source_mtime: 2020-01-01
    ingested: 2026-05-04
tags: [paper, scaling-law, kaplan, 2020]
last_updated: 2026-05-04
---

# Kaplan 2020 — Scaling Laws for Neural Language Models

## Claims as of source_mtime 2020-01-01

1. Test loss is a power law in compute: L(C) ∝ C^(-0.05).
2. Compute should be allocated **~73% to model size, ~27% to data** for compute-optimal training.
3. Once N is "right" for the compute budget, additional training tokens have diminishing returns.

## Status

**Superseded by Hoffmann 2022 (Chinchilla)** for compute allocation — see [Compute-optimal allocation](../concepts/compute_optimal_allocation.md). The Kaplan power-law form holds; the 73/27 prescription does not.

## Cross-references

- Newer view: [Hoffmann 2022 summary](hoffmann_2022_chinchilla.md).
- Concept that synthesizes both: [Compute-optimal allocation](../concepts/compute_optimal_allocation.md).
