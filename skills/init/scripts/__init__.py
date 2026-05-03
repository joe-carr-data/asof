"""asof:init — script package.

Modules:
    init           — CLI entry point, orchestrates the 5-stage wizard
    preflight      — stage 1: check Python / rsync / git / Obsidian
    wizard        — stage 2 + stage 5 interactive prompts (layout choice,
                     yes/no integrations)
    scaffold       — stages 3 + 4: create wiki dir + .asof.json + per-project
                     bootstrap pages from templates with placeholder substitution
    integrations   — stage 5: append CLAUDE.md snippet, install hook, edit
                     settings.json (defaults to .local for machine-portable
                     paths), optionally run first sync

Shared utilities (slugify, atomic_write_json, load_wiki_config, etc.) come
from the sync skill via a sys.path bootstrap at the top of each module that
needs them. Documented as a v1.x refactor target (extract to plugin-root
/lib/ once a third skill needs the same code).

The skill is invoked as: python3 ${CLAUDE_SKILL_DIR}/scripts/init.py [args]
"""
