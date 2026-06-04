import sys
from pathlib import Path

from mini_codex.gui import BrowserGuiState, agent_environment, build_agent_command, list_recent_transcripts, strip_ansi
from mini_codex.permissions import create_permission_profile, save_permission_profile


def test_build_agent_command_for_regular_run(tmp_path: Path):
    command = build_agent_command(sys.executable, workspace=tmp_path, task="fix tests")

    assert command == [sys.executable, "-m", "mini_codex", "--workspace", str(tmp_path), "fix tests"]


def test_build_agent_command_for_dry_run_uses_no_commands(tmp_path: Path):
    command = build_agent_command(sys.executable, workspace=tmp_path, task="inspect", dry_run=True)

    assert "--dry-run" in command
    assert "--no-commands" in command
    assert command[-1] == "inspect"


def test_build_agent_command_includes_permission_profile(tmp_path: Path):
    command = build_agent_command(
        sys.executable,
        workspace=tmp_path,
        task="fix",
        permission="restricted",
    )

    assert "--permission" in command
    assert command[command.index("--permission") + 1] == "restricted"
    assert command[-1] == "fix"


def test_agent_environment_prefers_unbuffered_output():
    env = agent_environment({"PATH": "x"})

    assert env["PATH"] == "x"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_list_recent_transcripts_returns_newest_first(tmp_path: Path):
    run_dir = tmp_path / ".mini_codex" / "runs"
    run_dir.mkdir(parents=True)
    older = run_dir / "older.md"
    newer = run_dir / "newer.md"
    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    older.touch()
    newer.touch()

    assert list_recent_transcripts(tmp_path, limit=1) == [newer]


def test_strip_ansi_removes_terminal_styles():
    assert strip_ansi("\x1b[31mError\x1b[0m") == "Error"


def test_browser_gui_state_exposes_permission_profiles(tmp_path: Path):
    profile = create_permission_profile("team", template="readonly")
    save_permission_profile(tmp_path, profile)
    state = BrowserGuiState(workspace=tmp_path)

    snapshot = state.snapshot()

    assert snapshot["permission_profiles"] == ["", "readonly", "restricted", "trusted", "team"]
