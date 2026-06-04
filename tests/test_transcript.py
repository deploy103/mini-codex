from pathlib import Path

from mini_codex.models import CommandResult
from mini_codex.transcript import RunTranscript


def test_run_transcript_records_task_and_result(tmp_path: Path):
    transcript = RunTranscript(tmp_path, task="do work")

    transcript.activity("look around")
    transcript.command(cmd="python -m pytest -q", why="verify", timeout=30)
    transcript.command_result(CommandResult(cmd="python -m pytest -q", returncode=0, stdout="ok\n", stderr=""))
    transcript.final(ok=True, iterations=1, message="done", elapsed_seconds=1.25)

    text = transcript.path.read_text(encoding="utf-8")
    assert "do work" in text
    assert "look around" in text
    assert "$ python -m pytest -q" in text
    assert "exit code: 0" in text
    assert "status: ok" in text
    assert "elapsed: 1.25s" in text
