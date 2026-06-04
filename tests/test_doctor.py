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
