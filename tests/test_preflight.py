from pathlib import Path

from mini_codex.preflight import inspect_git_state


def test_inspect_git_state_reports_non_repo(tmp_path: Path):
    state = inspect_git_state(tmp_path)

    assert state.command == "git status --short --branch"
    assert state.is_repo is False
    assert state.returncode != 0
    assert "git" in state.output or "repository" in state.output
