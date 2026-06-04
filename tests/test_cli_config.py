from pathlib import Path

from mini_codex.__main__ import (
    _extract_local_command,
    build_test_command,
    list_recent_transcripts,
    main,
    resolve_base_url_for_model,
)


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


def test_build_test_command_uses_current_python_and_pytest_quiet():
    command = build_test_command("python", ["tests/test_cli_config.py"])

    assert command == ["python", "-m", "pytest", "-q", "tests/test_cli_config.py"]


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


def test_main_logs_alias_does_not_require_api_key(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("APIM_KEY", raising=False)

    code = main(["--workspace", str(tmp_path), "logs"])

    captured = capsys.readouterr()
    assert code == 0
    assert "No run transcripts yet." in captured.out
