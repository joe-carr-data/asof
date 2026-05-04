# Kaplan et al. 2020 — Scaling Laws for Neural Language Models

**Citation**: Kaplan, Jared, et al. "Scaling laws for neural language models." *arXiv preprint arXiv:2001.08361* (2020).

## Headline result

Test loss as a function of compute (C), parameters (N), and dataset size (D) follows a power law. Performance is dominated by **compute**; given fixed compute budget, the best policy is to scale model parameters and use modestly-sized datasets.

## Key claims

1. L(C) ∝ C^(-0.05), so 10× compute → ~12% loss reduction.
2. Optimal allocation of fixed compute: ~73% to model size, ~27% to data — i.e., **prefer larger models over more data**.
3. Diminishing returns from training tokens once N is "right" for the compute budget.

## Why it mattered

This paper drove the 2020-2022 era of "make the model bigger" — GPT-3 (175B), Megatron, etc. The compute-allocation guidance was treated as load-bearing.
