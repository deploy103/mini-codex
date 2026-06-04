import subprocess
from pathlib import Path

import pytest

from mini_codex.__main__ import (
    _extract_local_command,
    _resume_observations,
    build_parser,
    build_test_command,
    list_recent_transcripts,
    main,
    resolve_base_url_for_model,
    run_logs_command,
    run_diff_command,
    run_test_command,
    run_task,
)
from mini_codex.console import Console


def test_resolve_base_url_for_model_rewrites_apim_model_segment():
    base_url = "https://gateway.example.test/foundry/gpt-5.4/"

    resolved = resolve_base_url_for_model(base_url, old_model="gpt-5.4", new_model="gpt-5.5")

    assert resolved == "https://gateway.example.test/foundry/gpt-5.5/"


def test_resolve_base_url_for_model_keeps_plain_openai_base_url():
    base_url = "https://api.openai.com/v1"

    resolved = resolve_base_url_for_model(base_url, old_model="gpt-5.4", new_model="gpt-5.5")

    assert resolved == base_url


def test_extract_local_config_alias_becomes_show_config():
    command = _extract_local_command(["--workspace", "/tmp/work", "config"])

    assert command == ("config", "/tmp/work", ["--workspace", "/tmp/work", "--show-config"])


def test_extract_local_logs_alias_does_not_become_model_task():
    command = _extract_local_command(["logs"])

    assert command == ("logs", ".", [])


def test_extract_local_status_alias_does_not_become_model_task():
    command = _extract_local_command(["--workspace", "/tmp/work", "status"])

    assert command == ("status", "/tmp/work", [])


def test_extract_local_diff_alias_does_not_become_model_task():
    command = _extract_local_command(["diff"])

    assert command == ("diff", ".", [])


def test_build_test_command_uses_current_python_and_pytest_quiet():
    command = build_test_command("python", ["tests/test_cli_config.py"])

    assert command == ["python", "-m", "pytest", "-q", "tests/test_cli_config.py"]


def test_parser_prints_version(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "mini-codex 0.1.0" in captured.out


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


def test_resume_observations_reads_latest_transcript(tmp_path: Path, capsys):
    run_dir = tmp_path / ".mini_codex" / "runs"
    run_dir.mkdir(parents=True)
    transcript = run_dir / "latest.md"
    transcript.write_text("# Run\nold context", encoding="utf-8")

    observations = _resume_observations(tmp_path, Console())

    captured = capsys.readouterr()
    assert len(observations) == 1
    assert "old context" in observations[0]
    assert "Resuming from transcript:" in captured.out


def test_main_logs_alias_does_not_require_api_key(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("APIM_KEY", raising=False)

    code = main(["--workspace", str(tmp_path), "logs"])

    captured = capsys.readouterr()
    assert code == 0
    assert "No run transcripts yet." in captured.out


def test_main_status_alias_does_not_require_api_key(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("APIM_KEY", raising=False)

    code = main(["--workspace", str(tmp_path), "status"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Workspace:" in captured.out
    assert "Latest transcript: none" in captured.out
    assert "Permission profiles:" in captured.out


def test_main_diff_alias_does_not_require_api_key(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("APIM_KEY", raising=False)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (tmp_path / "demo.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "demo.txt"], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    code = main(["--workspace", str(tmp_path), "diff"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Staged changes:" in captured.out
    assert "demo.txt" in captured.out


def test_run_diff_command_rejects_non_git_workspace(tmp_path: Path, capsys):
    code = run_diff_command(tmp_path, Console())

    captured = capsys.readouterr()
    assert code == 1
    assert "not a git repository" in captured.err


def test_run_task_rejects_missing_workspace(tmp_path: Path):
    args = type(
        "Args",
        (),
        {
            "workspace": str(tmp_path / "missing"),
            "model": None,
            "api_mode": None,
            "request_timeout": None,
            "permission": None,
        },
    )()

    with pytest.raises(RuntimeError, match="Workspace not found"):
        run_task("work", args, Console())


def test_run_test_command_rejects_missing_workspace(tmp_path: Path, capsys):
    code = run_test_command(tmp_path / "missing", [], Console())

    captured = capsys.readouterr()
    assert code == 1
    assert "Workspace not found" in captured.err


def test_run_logs_command_rejects_missing_workspace(tmp_path: Path, capsys):
    code = run_logs_command(tmp_path / "missing", Console())

    captured = capsys.readouterr()
    assert code == 1
    assert "Workspace not found" in captured.err
