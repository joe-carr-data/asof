---
title: Chinchilla scaling law
type: entity
project: scaling-laws
sources:
  - path: raw/scaling-laws/papers/hoffmann_2022_chinchilla.md
    source_mtime: 2022-03-15
    ingested: 2026-05-04
tags: [scaling-law, chinchilla, hoffmann]
aliases: [chinchilla, "Hoffmann's law"]
last_updated: 2026-05-04
---

# Chinchilla scaling law

The compute-optimal scaling law from Hoffmann 2022. States that for a given compute budget C, the optimal (N, D) pair satisfies roughly D ≈ 20 × N tokens-per-parameter.

## Practical heuristic

| Compute budget (FLOPs) | Optimal N | Optimal D |
|---|---|---|
| 6.0e23 | ~70B params | ~1.4T tokens |
| 1.5e23 | ~28B params | ~560B tokens |
| 6.0e22 | ~14B params | ~280B tokens |

The sweet spot is determined empirically by Chinchilla's compute sweep across model sizes 70M to 16B.

## Cross-references

- Source: [Hoffmann 2022 summary](../sources/hoffmann_2022_chinchilla.md).
- Concept (synthesizes vs. Kaplan 2020): [Compute-optimal allocation](../concepts/compute_optimal_allocation.md).
