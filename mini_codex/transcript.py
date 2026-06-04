from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import CommandResult


class RunTranscript:
    def __init__(self, workspace: Path, *, task: str) -> None:
        self.workspace = workspace.resolve()
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        self.path = self.workspace / ".mini_codex" / "runs" / f"{stamp}.md"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._append(
            "\n".join(
                [
                    f"# mini-codex run {stamp}",
                    "",
                    "## Task",
                    "",
                    _fence(task),
                    "",
                ]
            )
        )

    def activity(self, message: str) -> None:
        self._append(f"## Activity - {_timestamp()}\n\n- {message}\n\n")

    def text(self, title: str, body: str) -> None:
        self._append(f"## {title} - {_timestamp()}\n\n{body.rstrip()}\n\n")

    def command(self, *, cmd: str, why: str, timeout: int) -> None:
        lines = [f"## Command - {_timestamp()}", "", _fence(f"$ {cmd}\nwhy: {why or '(not provided)'}\ntimeout: {timeout}s")]
        self._append("\n".join(lines) + "\n\n")

    def command_result(self, result: CommandResult) -> None:
        if result.skipped:
            body = f"SKIPPED: {result.blocked_reason or result.stderr}"
        else:
            chunks = [f"exit code: {result.returncode}", f"duration: {result.duration_seconds:.2f}s"]
            if result.stdout:
                chunks.append(f"stdout:\n{result.stdout}")
            if result.stderr:
                chunks.append(f"stderr:\n{result.stderr}")
            body = "\n".join(chunks)
        self._append(f"## Command Result - {_timestamp()}\n\n{_fence(body)}\n\n")

    def final(self, *, ok: bool, iterations: int, message: str, elapsed_seconds: float = 0.0) -> None:
        status = "ok" if ok else "failed"
        self._append(
            f"## Final Result - {_timestamp()}\n\n"
            f"- status: {status}\n"
            f"- iterations: {iterations}\n"
            f"- elapsed: {elapsed_seconds:.2f}s\n"
            f"- message: {message}\n"
        )

    def _append(self, text: str) -> None:
        with self.path.open("a", encoding="utf-8", newline="") as file:
            file.write(text)


def _fence(text: str) -> str:
    return "```text\n" + text.replace("```", "'''") + "\n```"


def _timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
