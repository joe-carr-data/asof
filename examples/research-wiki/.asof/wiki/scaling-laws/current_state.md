---
title: scaling-laws — current state
type: overview
project: scaling-laws
sources:
  - path: raw/scaling-laws/papers/hoffmann_2022_chinchilla.md
    source_mtime: 2022-03-15
    ingested: 2026-05-04
tags: [overview, snapshot]
last_updated: 2026-05-04
---

# scaling-laws — current state

## Snapshot date

2026-05-04 — wiki seeded with two foundational scaling-law papers (Kaplan 2020, Hoffmann 2022).

## TL;DR

Compute-optimal LLM training requires scaling model size (N) and training tokens (D) **roughly equally** — about 20 tokens per parameter (Hoffmann 2022, "Chinchilla"). The earlier Kaplan 2020 prescription of 73% compute → params, 27% → data is superseded; Kaplan 2020's flaw was a fixed learning-rate schedule across model sizes that biased the optimum toward larger models.

## Active law

[Chinchilla scaling law](entities/chinchilla_scaling_law.md): D ≈ 20 × N.

## Known supersession

[Compute-optimal allocation](concepts/compute_optimal_allocation.md) explicitly notes Kaplan 2020 → Hoffmann 2022 supersession of the allocation prescription. The Kaplan 2020 power-law functional form for L(C) still holds.

## See also

- [index.md](index.md) — full catalog
