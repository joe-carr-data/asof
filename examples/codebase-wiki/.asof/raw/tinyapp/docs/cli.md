# tinyapp CLI

```
tinyapp <file.csv> [--columns col1,col2] [--median-window N]
```

| Flag | Default | Effect |
|---|---|---|
| `<file.csv>` (positional) | required | Input CSV. UTF-8, comma-delimited, header row required. |
| `--columns col1,col2` | all numeric columns | Restrict to these columns. |
| `--median-window N` | 10000 | Reservoir size for median approximation (see `architecture.md`). |
| `--no-median` | False | Skip median (faster on wide files). |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Stats printed to stdout. |
| 1 | File not found / unreadable. |
| 2 | Header missing or columns malformed. |
| 3 | No numeric columns found. |
