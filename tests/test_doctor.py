import subprocess
from pathlib import Path

from mini_codex.__main__ import build_parser, run_doctor
from mini_codex.console import Console


def test_run_doctor_reports_ready_setup(tmp_path: Path, monkeypatch, capsys):
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_API_MODE",
        "APIM_KEY",
        "APIM_BASE_URL",
        "CHAT_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "APIM_BASE_URL=https://gateway.example.test",
                "APIM_KEY=test-key",
                "CHAT_MODEL=test-model",
            ]
        ),
        encoding="utf-8",
    )
    args = build_parser().parse_args(["--workspace", str(tmp_path), "--doctor"])

    code = run_doctor(args, Console())

    captured = capsys.readouterr()
    assert code == 0
    assert "[ok] Workspace" in captured.out
    assert "[ok] Config loaded" in captured.out
    assert "test-key" not in captured.out


def test_run_doctor_stops_on_missing_workspace(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("APIM_KEY", "test-key")
    monkeypatch.setenv("APIM_BASE_URL", "https://gateway.example.test")
    monkeypatch.setenv("CHAT_MODEL", "test-model")
    args = build_parser().parse_args(["--workspace", str(tmp_path / "missing"), "--doctor"])

    code = run_doctor(args, Console())

    captured = capsys.readouterr()
    assert code == 1
    assert "Workspace not found" in captured.err
    assert "Config loaded" not in captured.out


def test_run_doctor_checks_env_is_gitignored(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("APIM_KEY", raising=False)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "APIM_BASE_URL=https://gateway.example.test\nAPIM_KEY=test-key\nCHAT_MODEL=test-model\n",
        encoding="utf-8",
    )
    args = build_parser().parse_args(["--workspace", str(tmp_path), "--doctor"])

    code = run_doctor(args, Console())

    captured = capsys.readouterr()
    assert code == 0
    assert ".env git ignore" in captured.out


def test_run_doctor_fails_when_env_is_not_gitignored(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("APIM_KEY", raising=False)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (tmp_path / ".env").write_text(
        "APIM_BASE_URL=https://gateway.example.test\nAPIM_KEY=test-key\nCHAT_MODEL=test-model\n",
        encoding="utf-8",
    )
    args = build_parser().parse_args(["--workspace", str(tmp_path), "--doctor"])

    code = run_doctor(args, Console())

    captured = capsys.readouterr()
    assert code == 1
    assert ".env is not ignored" in captured.err
