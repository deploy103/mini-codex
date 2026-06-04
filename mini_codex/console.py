from __future__ import annotations

import sys
from textwrap import indent

try:
    from rich.console import Console as RichConsole
except ImportError:  # pragma: no cover
    RichConsole = None


DEFAULT_OUTPUT_LIMIT = 4_000


class Console:
    def __init__(self) -> None:
        self._rich = RichConsole() if RichConsole is not None else None
        self._rich_err = RichConsole(stderr=True) if RichConsole is not None else None

    def info(self, message: str) -> None:
        self._print(message)

    def activity(self, message: str) -> None:
        self._print(f"\n[activity] {message}", style="bold cyan")

    def plan_item(self, message: str) -> None:
        self._print(f"  - {message}")

    def command(self, cmd: str, *, why: str = "", timeout: int | None = None) -> None:
        details = []
        if why:
            details.append(f"why: {why}")
        if timeout is not None:
            details.append(f"timeout: {timeout}s")

        self._print("")
        self._print(f"$ {cmd}", style="bold")
        for detail in details:
            self._print(f"  {detail}")

    def command_output(self, stream_name: str, text: str, *, output_limit: int = DEFAULT_OUTPUT_LIMIT) -> None:
        text = _truncate_for_console(text, output_limit)
        if not text:
            return
        prefix = "stderr" if stream_name == "stderr" else "stdout"
        for line in text.rstrip().splitlines():
            self._print(f"{prefix}: {line}")

    def command_result(
        self,
        *,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        skipped: bool = False,
        blocked_reason: str | None = None,
        duration_seconds: float | None = None,
        output_limit: int = DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        if skipped:
            self.warn(f"skipped: {blocked_reason or stderr or 'Skipped by local runner settings.'}")
            return

        duration = f" in {duration_seconds:.2f}s" if duration_seconds is not None else ""
        if returncode == 0:
            self.info(f"result: passed{duration}")
        else:
            self.warn(f"result: failed with exit code {returncode}{duration}")

        stdout = _truncate_for_console(stdout, output_limit)
        stderr = _truncate_for_console(stderr, output_limit)
        if stdout:
            self.info("stdout:\n" + indent(stdout.rstrip(), "  "))
        if stderr:
            self.info("stderr:\n" + indent(stderr.rstrip(), "  "))

    def warn(self, message: str) -> None:
        self._print(f"Warning: {message}", style="yellow")

    def error(self, message: str) -> None:
        self._print(f"Error: {message}", style="bold red", err=True)

    def rule(self, title: str) -> None:
        if self._rich is not None:
            self._rich.rule(title)
        else:
            self._print(f"\n== {title} ==")

    def _print(self, message: str, style: str | None = None, err: bool = False) -> None:
        if self._rich is not None:
            target = self._rich_err if err and self._rich_err is not None else self._rich
            target.print(message, style=style, markup=False)
            return
        print(message, file=sys.stderr if err else sys.stdout)


def _truncate_for_console(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... output truncated ..."
