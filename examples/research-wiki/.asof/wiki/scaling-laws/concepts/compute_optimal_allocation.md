---
title: Compute-optimal allocation
type: concept
project: scaling-laws
sources:
  - path: raw/scaling-laws/papers/kaplan_2020_scaling_laws.md
    source_mtime: 2020-01-01
    ingested: 2026-05-04
  - path: raw/scaling-laws/papers/hoffmann_2022_chinchilla.md
    source_mtime: 2022-03-15
    ingested: 2026-05-04
tags: [scaling, compute, optimization]
last_updated: 2026-05-04
---

# Compute-optimal allocation

How to split a fixed compute budget between model parameters (N) and training tokens (D).

## Current view (per Hoffmann 2022 / Chinchilla)

For compute-optimal training, model size and dataset size should scale **roughly equally**: about 20 tokens per parameter. Most pre-2022 large models were under-trained — too many parameters for too few tokens.

In practice: given compute budget C, search over (N, D) such that N × D × 6 ≈ C and N/D ≈ 1/20, then pick the (N, D) pair that minimizes test loss.

## Cross-source supersession

Previously (Kaplan 2020): the prescription was 73% of compute → parameters, 27% → data, with diminishing returns from extra tokens once N was "right". This is **superseded by** Hoffmann 2022 (a *different* source, per SCHEMA §6.4 — not the same source re-ingested with a newer mtime, which would be §6.5 self-supersession). The Kaplan 2020 sweep used a fixed learning-rate schedule across model sizes, biasing the optimum toward larger models. Once the LR schedule varies per size, the optimum collapses to the ~50/50 compute split that Chinchilla reports.

The Kaplan 2020 power-law functional form for L(C) still holds — only the allocation prescription was overturned.

## Cross-references

- Original view: [Kaplan 2020 summary](../sources/kaplan_2020_scaling_laws.md).
- Newer view: [Hoffmann 2022 summary](../sources/hoffmann_2022_chinchilla.md).
- Entity that depends on this: [Chinchilla scaling law](../entities/chinchilla_scaling_law.md).
