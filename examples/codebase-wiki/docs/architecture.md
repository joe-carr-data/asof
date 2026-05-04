# tinyapp architecture

Three-layer design:

1. **CLI parser** (`cli.py`) — argparse, validates input file path.
2. **Reader** (`reader.py`) — streaming CSV reader; one row at a time so memory stays O(columns), not O(rows).
3. **Stats engine** (`stats.py`) — running mean/variance via Welford's algorithm; median via reservoir of size `--median-window` (default 10000).

## Why streaming?

The original v0.1 prototype loaded the full file with `pandas.read_csv()`. That worked for files under a few hundred MB but OOM'd on the multi-GB inputs the user actually had. v0.2 switched to streaming — `pandas` is no longer a dependency.

## Median approximation

True median requires sorting; sorting requires the full dataset in memory. We approximate via a fixed-size reservoir: each column maintains a `--median-window`-sized sample, and the reported median is the median of the reservoir. Accurate to within ~1% on the test corpus.
