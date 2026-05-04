"""End-to-end resolution of wiki_dir across the full chain:
    --wiki-dir flag → ASOF_DIR env → walk-up Pattern C → walk-up bare
    config → DEFAULT_WIKI_DIR (Pattern A).

Each branch verified by invoking lint as a real subprocess. Lint shares
sync's resolver (Codex round-1 phase-4 LATENT fix), so this also covers
sync's resolution behavior implicitly.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import run_skill

# ─── 1. --wiki-dir flag wins ──────────────────────────────────────────────


def test_explicit_flag_overrides_everything(
    pattern_a_wiki: Path, tmp_path: Path
) -> None:
    """--wiki-dir flag wins even when ASOF_DIR points elsewhere AND cwd
    is inside a different Pattern C repo."""
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    result = run_skill(
        "lint",
        ["--wiki-dir", str(pattern_a_wiki)],
        cwd=decoy,
        env={"ASOF_DIR": str(decoy)},
    )
    assert result.returncode == 0, result.stderr
    assert str(pattern_a_wiki) in result.stdout


# ─── 2. ASOF_DIR env ──────────────────────────────────────────────────────


def test_asof_dir_env_resolves_when_no_flag(
    pattern_a_wiki: Path, tmp_path: Path
) -> None:
    """No --wiki-dir; cwd is unrelated; ASOF_DIR points to the wiki."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = run_skill(
        "lint",
        cwd=elsewhere,
        env={"ASOF_DIR": str(pattern_a_wiki)},
    )
    assert result.returncode == 0, result.stderr
    assert str(pattern_a_wiki) in result.stdout


# ─── 3. walk-up Pattern C (cwd inside repo with .asof/) ────────────────────


def test_walks_up_to_pattern_c_marker(pattern_c_wiki: Path) -> None:
    """No flag, no env. cwd is several levels deep inside a Pattern C
    repo; lint must walk up to find <repo>/.asof/.asof.json."""
    deep = pattern_c_wiki.parent / "a" / "b" / "c"
    deep.mkdir(parents=True)
    result = run_skill("lint", cwd=deep, env={"ASOF_DIR": ""})
    assert result.returncode == 0, result.stderr
    assert str(pattern_c_wiki) in result.stdout


# ─── 4. walk-up bare .asof.json (Pattern A wiki sitting in cwd tree) ───────


def test_walks_up_to_bare_config(pattern_a_wiki: Path) -> None:
    """If cwd is inside a directory tree whose root contains .asof.json
    directly (Pattern A/B with that dir as the wiki_dir), resolver
    finds it via walk-up. Different from Pattern C (which expects
    <dir>/.asof/.asof.json)."""
    deep = pattern_a_wiki / "wiki" / "myproj" / "entities"
    # entities/ exists from init; create a deeper dir to walk up from.
    nest = deep / "x" / "y"
    nest.mkdir(parents=True)
    result = run_skill("lint", cwd=nest, env={"ASOF_DIR": ""})
    assert result.returncode == 0, result.stderr
    assert str(pattern_a_wiki) in result.stdout


# ─── 5. DEFAULT_WIKI_DIR fallback (no flag, no env, no walk-up match) ──────


def test_default_fallback_when_no_resolution(
    tmp_path: Path,
) -> None:
    """No flag; ASOF_DIR cleared; cwd is empty unrelated dir. Resolver
    returns ~/.claude/asof; load_wiki_config then surfaces the missing-
    config error → exit 4 (PRECONDITION). Same exit code, more
    informative error path than the previous local resolver's."""
    empty = tmp_path / "no-wiki-here"
    empty.mkdir()
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    result = run_skill(
        "lint",
        cwd=empty,
        env={"ASOF_DIR": "", "HOME": str(fake_home)},
    )
    assert result.returncode == 4, result.stderr
    assert "no asof config" in result.stderr


# ─── flag overrides env ────────────────────────────────────────────────────


def test_flag_beats_env_for_lint(pattern_a_wiki: Path, tmp_path: Path) -> None:
    """Both --wiki-dir and ASOF_DIR are set, but they disagree. Flag wins."""
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    result = run_skill(
        "lint",
        ["--wiki-dir", str(pattern_a_wiki)],
        env={"ASOF_DIR": str(bogus)},
    )
    assert result.returncode == 0, result.stderr
    assert str(pattern_a_wiki) in result.stdout
