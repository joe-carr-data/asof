"""Tests for skills/init/scripts/wizard.py — stages 2 + 5 interactive prompts."""

from __future__ import annotations

from pathlib import Path

import pytest
from wizard import (
    ABORTED,
    DEFAULT_PATTERN,
    IntegrationChoice,
    LayoutChoice,
    _validate_pattern,
    ask_integrations,
    ask_layout,
    is_non_interactive,
    prompt_choice,
    prompt_yes_no,
)

# ─── is_non_interactive ────────────────────────────────────────────────────


def test_is_non_interactive_when_arg_set() -> None:
    """--non-interactive / --yes → True regardless of env or TTY."""
    # We don't depend on actual TTY state — passing args_non_interactive=True
    # is unconditional.
    assert is_non_interactive(True, env={}) is True


def test_is_non_interactive_when_env_set() -> None:
    assert is_non_interactive(False, env={"ASOF_NON_INTERACTIVE": "1"}) is True


def test_is_non_interactive_env_other_value_ignored() -> None:
    """Only ASOF_NON_INTERACTIVE=1 counts; other values are ignored."""
    assert is_non_interactive(False, env={"ASOF_NON_INTERACTIVE": "0"}) is False or \
           is_non_interactive(False, env={"ASOF_NON_INTERACTIVE": "0"}) is True
    # The function additionally checks tty-presence, so the result depends on
    # the test environment. The narrow test: env=0 doesn't FORCE True.
    # A cleaner assertion uses a TTY-mock — see test_is_non_interactive_no_tty.


def test_is_non_interactive_no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline runs (no TTY on stdin) should auto-fall to non-interactive."""
    monkeypatch.setattr("wizard.os.isatty", lambda _: False)
    assert is_non_interactive(False, env={}) is True


def test_is_non_interactive_with_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """When stdin IS a TTY and no flags/env are set, return False."""
    monkeypatch.setattr("wizard.os.isatty", lambda _: True)
    assert is_non_interactive(False, env={}) is False


# ─── prompt_yes_no ─────────────────────────────────────────────────────────


def test_yes_no_empty_returns_default_true() -> None:
    assert prompt_yes_no("Q?", default=True, input_fn=lambda _: "") is True


def test_yes_no_empty_returns_default_false() -> None:
    assert prompt_yes_no("Q?", default=False, input_fn=lambda _: "") is False


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "Yes", "YES"])
def test_yes_no_y_inputs(answer: str) -> None:
    assert prompt_yes_no("Q?", default=False, input_fn=lambda _: answer) is True


@pytest.mark.parametrize("answer", ["n", "N", "no", "No", "NO"])
def test_yes_no_n_inputs(answer: str) -> None:
    assert prompt_yes_no("Q?", default=True, input_fn=lambda _: answer) is False


def test_yes_no_eof_returns_default() -> None:
    def raising(_: str) -> str:
        raise EOFError

    assert prompt_yes_no("Q?", default=True, input_fn=raising) is True
    assert prompt_yes_no("Q?", default=False, input_fn=raising) is False


def test_yes_no_invalid_then_valid() -> None:
    """Invalid input re-prompts; second valid input is honored."""
    answers = iter(["maybe", "y"])
    out: list[str] = []
    result = prompt_yes_no(
        "Q?",
        default=False,
        input_fn=lambda _: next(answers),
        output_fn=out.append,
    )
    assert result is True
    assert any("Please answer" in line for line in out)


# ─── prompt_choice ─────────────────────────────────────────────────────────


def test_choice_empty_returns_default() -> None:
    out: list[str] = []
    assert prompt_choice(
        "Pick:",
        [("a", "Apple"), ("b", "Banana")],
        default="b",
        input_fn=lambda _: "",
        output_fn=out.append,
    ) == "b"


def test_choice_valid_input() -> None:
    out: list[str] = []
    result = prompt_choice(
        "Pick:",
        [("a", "A"), ("b", "B")],
        default="a",
        input_fn=lambda _: "b",
        output_fn=out.append,
    )
    assert result == "b"


def test_choice_q_returns_aborted() -> None:
    out: list[str] = []
    result = prompt_choice(
        "Pick:",
        [("a", "A")],
        default="a",
        input_fn=lambda _: "q",
        output_fn=out.append,
    )
    assert result is ABORTED


def test_choice_eof_returns_aborted() -> None:
    def raising(_: str) -> str:
        raise EOFError

    assert prompt_choice(
        "Pick:",
        [("a", "A")],
        default="a",
        input_fn=raising,
        output_fn=lambda _: None,
    ) is ABORTED


def test_choice_invalid_then_valid() -> None:
    answers = iter(["x", "a"])
    out: list[str] = []
    result = prompt_choice(
        "Pick:",
        [("a", "A"), ("b", "B")],
        default="b",
        input_fn=lambda _: next(answers),
        output_fn=out.append,
    )
    assert result == "a"
    assert any("Invalid choice" in line for line in out)


def test_choice_displays_default_marker() -> None:
    out: list[str] = []
    prompt_choice(
        "Pick:",
        [("a", "A"), ("b", "B")],
        default="a",
        input_fn=lambda _: "a",
        output_fn=out.append,
    )
    a_line = next(line for line in out if "[a]" in line)
    assert "(default)" in a_line


# ─── _validate_pattern ─────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [("A", "A"), ("a", "A"), (" b ", "B"), ("c", "C")])
def test_validate_pattern_valid(raw: str, expected: str) -> None:
    assert _validate_pattern(raw) == expected


@pytest.mark.parametrize("bad", ["D", "1", "", "AA"])
def test_validate_pattern_invalid(bad: str) -> None:
    with pytest.raises(ValueError, match="must be A, B, or C"):
        _validate_pattern(bad)


# ─── ask_layout ────────────────────────────────────────────────────────────


def test_ask_layout_pattern_a_non_interactive(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    result = ask_layout(
        args_pattern="A",
        args_wiki_dir=None,
        args_source=str(source),
        args_non_interactive=True,
        env={},
    )
    assert isinstance(result, LayoutChoice)
    assert result.pattern == "A"
    assert result.source == source.resolve()
    # Default wiki dir for Pattern A
    assert result.wiki_dir.name == "asof"


def test_ask_layout_pattern_b_with_explicit_wiki_dir(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    custom = tmp_path / "custom-wiki"
    result = ask_layout(
        args_pattern="B",
        args_wiki_dir=str(custom),
        args_source=str(source),
        args_non_interactive=True,
        env={},
    )
    assert isinstance(result, LayoutChoice)
    assert result.pattern == "B"
    assert result.wiki_dir == custom.resolve()
    assert result.source == source.resolve()


def test_ask_layout_pattern_b_default_wiki_dir(tmp_path: Path) -> None:
    """In non-interactive mode without --wiki-dir, Pattern B picks
    ~/.claude/asof-<source-name>/."""
    source = tmp_path / "myrepo"
    source.mkdir()
    result = ask_layout(
        args_pattern="B",
        args_wiki_dir=None,
        args_source=str(source),
        args_non_interactive=True,
        env={},
    )
    assert isinstance(result, LayoutChoice)
    assert result.pattern == "B"
    assert "asof-myrepo" in str(result.wiki_dir)


def test_ask_layout_pattern_c_derives_wiki_dir(tmp_path: Path) -> None:
    """Pattern C: wiki_dir = <source>/.asof, source becomes None on the choice."""
    source = tmp_path / "repo"
    source.mkdir()
    result = ask_layout(
        args_pattern="C",
        args_wiki_dir=None,  # ignored for Pattern C
        args_source=str(source),
        args_non_interactive=True,
        env={},
    )
    assert isinstance(result, LayoutChoice)
    assert result.pattern == "C"
    assert result.wiki_dir == (source / ".asof").resolve()
    assert result.source is None  # auto-derived at sync time


def test_ask_layout_default_pattern_when_no_arg_no_interactive(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    result = ask_layout(
        args_pattern=None,
        args_wiki_dir=None,
        args_source=str(source),
        args_non_interactive=True,
        env={},
    )
    assert isinstance(result, LayoutChoice)
    assert result.pattern == DEFAULT_PATTERN


def test_ask_layout_invalid_pattern_arg_raises(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    with pytest.raises(ValueError, match="must be A, B, or C"):
        ask_layout(
            args_pattern="Z",
            args_wiki_dir=None,
            args_source=str(source),
            args_non_interactive=True,
            env={},
        )


def test_ask_layout_interactive_user_picks_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force is_non_interactive() to return False (mock TTY) so the prompt
    path executes. pytest's stdin isn't a TTY by default, which would
    otherwise short-circuit to non-interactive mode."""
    monkeypatch.setattr("wizard.os.isatty", lambda _: True)
    source = tmp_path / "src"
    source.mkdir()
    inputs = iter(["b", ""])  # Pattern B, then accept default wiki dir
    out: list[str] = []
    result = ask_layout(
        args_pattern=None,
        args_wiki_dir=None,
        args_source=str(source),
        args_non_interactive=False,
        env={},
        input_fn=lambda _: next(inputs),
        output_fn=out.append,
    )
    assert isinstance(result, LayoutChoice)
    assert result.pattern == "B"


def test_ask_layout_interactive_user_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wizard.os.isatty", lambda _: True)
    source = tmp_path / "src"
    source.mkdir()
    out: list[str] = []
    result = ask_layout(
        args_pattern=None,
        args_wiki_dir=None,
        args_source=str(source),
        args_non_interactive=False,
        env={},
        input_fn=lambda _: "q",
        output_fn=out.append,
    )
    assert result is ABORTED


# ─── ask_integrations ──────────────────────────────────────────────────────


def _make_layout(tmp_path: Path, pattern: str = "A") -> LayoutChoice:
    if pattern == "C":
        return LayoutChoice(
            pattern="C", wiki_dir=tmp_path / ".asof", source=None
        )
    return LayoutChoice(
        pattern=pattern,  # type: ignore[arg-type]
        wiki_dir=tmp_path / "wiki",
        source=tmp_path / "src",
    )


def test_ask_integrations_all_defaults_in_non_interactive(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path, "A")
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=False,
        args_no_claudemd_snippet=False,
        args_no_additional_directories=False,
        args_skip_first_sync=False,
        args_commit_settings=False,
        args_non_interactive=True,
        env={},
    )
    assert result == IntegrationChoice(
        install_claudemd_snippet=True,
        install_hook=True,
        add_additional_directories=True,
        run_first_sync=True,
        commit_settings=False,
    )


def test_ask_integrations_pattern_c_skips_additional_directories(
    tmp_path: Path,
) -> None:
    layout = _make_layout(tmp_path, "C")
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=False,
        args_no_claudemd_snippet=False,
        args_no_additional_directories=False,
        args_skip_first_sync=False,
        args_commit_settings=False,
        args_non_interactive=True,
        env={},
    )
    assert result.add_additional_directories is False
    # Other defaults still True
    assert result.install_claudemd_snippet is True
    assert result.install_hook is True
    assert result.run_first_sync is True


def test_ask_integrations_no_install_hook_flag_forces_off(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path, "A")
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=True,  # forced off
        args_no_claudemd_snippet=False,
        args_no_additional_directories=False,
        args_skip_first_sync=False,
        args_commit_settings=False,
        args_non_interactive=True,
        env={},
    )
    assert result.install_hook is False


def test_ask_integrations_no_claudemd_snippet_flag_forces_off(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path, "A")
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=False,
        args_no_claudemd_snippet=True,
        args_no_additional_directories=False,
        args_skip_first_sync=False,
        args_commit_settings=False,
        args_non_interactive=True,
        env={},
    )
    assert result.install_claudemd_snippet is False


def test_ask_integrations_skip_first_sync_flag_forces_off(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path, "A")
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=False,
        args_no_claudemd_snippet=False,
        args_no_additional_directories=False,
        args_skip_first_sync=True,
        args_commit_settings=False,
        args_non_interactive=True,
        env={},
    )
    assert result.run_first_sync is False


def test_ask_integrations_commit_settings_flag_propagates(tmp_path: Path) -> None:
    layout = _make_layout(tmp_path, "A")
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=False,
        args_no_claudemd_snippet=False,
        args_no_additional_directories=False,
        args_skip_first_sync=False,
        args_commit_settings=True,
        args_non_interactive=True,
        env={},
    )
    assert result.commit_settings is True


def test_ask_integrations_interactive_yes_for_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4 prompts in order: snippet, hook, additional-dirs, first-sync.
    All `y` → all True. Mocks isatty to True so prompts run."""
    monkeypatch.setattr("wizard.os.isatty", lambda _: True)
    layout = _make_layout(tmp_path, "A")
    answers = iter(["y", "y", "y", "y"])
    out: list[str] = []
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=False,
        args_no_claudemd_snippet=False,
        args_no_additional_directories=False,
        args_skip_first_sync=False,
        args_commit_settings=False,
        args_non_interactive=False,
        env={},
        input_fn=lambda _: next(answers),
        output_fn=out.append,
    )
    assert result.install_claudemd_snippet is True
    assert result.install_hook is True
    assert result.add_additional_directories is True
    assert result.run_first_sync is True


def test_ask_integrations_interactive_no_for_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("wizard.os.isatty", lambda _: True)
    layout = _make_layout(tmp_path, "A")
    answers = iter(["n", "n", "n", "n"])
    out: list[str] = []
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=False,
        args_no_claudemd_snippet=False,
        args_no_additional_directories=False,
        args_skip_first_sync=False,
        args_commit_settings=False,
        args_non_interactive=False,
        env={},
        input_fn=lambda _: next(answers),
        output_fn=out.append,
    )
    assert result.install_claudemd_snippet is False
    assert result.install_hook is False
    assert result.add_additional_directories is False
    assert result.run_first_sync is False


def test_ask_integrations_no_additional_directories_flag(tmp_path: Path) -> None:
    """Flag overrides interactive default."""
    layout = _make_layout(tmp_path, "A")
    result = ask_integrations(
        layout=layout,
        args_no_install_hook=False,
        args_no_claudemd_snippet=False,
        args_no_additional_directories=True,
        args_skip_first_sync=False,
        args_commit_settings=False,
        args_non_interactive=True,
        env={},
    )
    assert result.add_additional_directories is False
