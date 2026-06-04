from mini_codex.console import Console


def test_console_smoke(capsys):
    console = Console()

    console.activity("doing work")
    console.command("python -m pytest -q", why="verify", timeout=30)
    console.command_output("stdout", "running\n")
    console.command_result(returncode=0, stdout="ok\n")
    console.error("failed")

    captured = capsys.readouterr()
    assert "[activity]" in captured.out
    assert "doing work" in captured.out
    assert "$ python -m pytest -q" in captured.out
    assert "stdout: running" in captured.out
    assert "result: passed" in captured.out
    assert "failed" in captured.err
