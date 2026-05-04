# asof examples

Three small wikis demonstrating the asof workflow. Each is **Pattern C** — the wiki lives at `<repo>/.asof/`, the committed `.asof.json` omits machine-local paths, and a fresh checkout lints clean from any directory.

| Example | Demonstrates | Sources |
|---|---|---|
| [codebase-wiki](codebase-wiki/) | Project docs for a tiny CLI; entity + concept + 3 source-summaries; cross-linked index | `README.md`, `docs/architecture.md`, `docs/cli.md` |
| [research-wiki](research-wiki/) | Paper-summary + **explicit supersession** (Kaplan 2020 → Hoffmann 2022) with mtimes spanning 2 years | Two scaling-law papers (Kaplan 2020, Hoffmann 2022) |
| [book-wiki](book-wiki/) | Book club notes; entities (System 1, System 2) + concept (Cognitive ease) | Part I + Part II reading notes |

## How to use these as templates

1. Copy the example dir into your own repo: `cp -r examples/codebase-wiki/ my-project/`
2. Edit `.asof/.asof.json` — change `"name": "tinyapp"` to your project's slug.
3. Replace the source files (`README.md`, `docs/`, etc.) with your own content.
4. Replace the wiki pages under `.asof/wiki/<your-slug>/` to describe your sources.
5. Run `/asof:sync` to ingest deltas and `/asof:lint` to audit.

## Verifying portability

Every example is exercised by `.github/workflows/lint-examples.yml` on every push. The workflow runs:

```bash
python3 skills/lint/scripts/lint.py --wiki-dir examples/<name>/.asof --severity warn
```

If you can lint them from a fresh clone of this repo, they're portable. CI does exactly that.

## Schema features per example

- **codebase-wiki**: source-summary citations, entity/concept cross-references, index curation.
- **research-wiki**: explicit cross-source supersession via `## Cross-source supersession` heading + "superseded by" prose (SCHEMA §6.4: a *different* source supersedes an older one — not §6.5 self-supersession, which is the same source re-ingested with a newer mtime). Lint's supersession-gap WARN trigger is exercised: gap = ~26 months between Kaplan and Hoffmann mtimes.
- **book-wiki**: aliased entities (System 1 / "fast thinking" / "intuitive system"), tags, project-relative links between sources / entities / concepts.

## Run locally

From the repo root:

```bash
# Lint one example
python3 skills/lint/scripts/lint.py --wiki-dir examples/codebase-wiki/.asof

# Lint all three (mirrors what CI does)
for w in codebase-wiki research-wiki book-wiki; do
  python3 skills/lint/scripts/lint.py --wiki-dir examples/$w/.asof --severity warn
done
```
