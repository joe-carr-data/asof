"""asof:sync — script package.

Modules:
    utils         — slug, path-safety, atomic writes, file lock, version discovery
    config        — .asof.json loading, wiki/project resolution, version compat matrix
    delta         — frontmatter parsing + delta detection (NEW / MODIFIED / DELETED)
    rsync_runner  — rsync invocation with safe-links default, exclude validation, self-ingest guard
    report        — human-readable report + JSON delta serialization
    sync          — CLI entry point, orchestrates the above

The skill is invoked as: python3 ${CLAUDE_SKILL_DIR}/scripts/sync.py [args]
"""
