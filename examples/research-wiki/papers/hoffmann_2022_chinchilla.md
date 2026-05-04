# Hoffmann et al. 2022 — Training Compute-Optimal Large Language Models (Chinchilla)

**Citation**: Hoffmann, Jordan, et al. "Training compute-optimal large language models." *arXiv preprint arXiv:2203.15556* (2022).

## Headline result

Refines Kaplan 2020's compute-allocation prescription. **Most existing large models are significantly under-trained.** For compute-optimal training, model size and dataset size should scale roughly **equally** (1:1), not 73:27 as Kaplan suggested.

## Key claims (supersedes Kaplan 2020)

1. Optimal model: 70B parameters, 1.4T tokens — given the same compute budget previously used for ~280B-param models trained on ~300B tokens.
2. The 73/27 compute-allocation rule from Kaplan 2020 was based on a flawed sweep that fixed the learning-rate schedule across model sizes; Chinchilla varies the schedule per size and recovers a markedly different optimum.
3. **Compute-equal allocation**: roughly N ≈ D / 20 tokens-per-parameter for compute-optimal training.

## Why it mattered

Reset the field's "bigger is better" default. Post-Chinchilla, training runs increased data budgets and slowed parameter-count growth. Kaplan's prescription is now treated as the historical baseline, not current guidance.
