from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .models import CommandResult, ShellCommand
from .permissions import PermissionProfile, permission_denial_reason


DANGEROUS_PATTERNS = [
    r"\bsudo\b",
    r"\brm\b(?=[^\n;|&]*(?:-[^\s\n;|&]*r|--recursive))(?=[^\n;|&]*(?:-[^\s\n;|&]*f|--force))",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[^\n;|&]*f",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bformat\b",
    r"\bdiskpart\b",
    r"Remove-Item\s+.*-Recurse\s+.*-Force",
    r":\(\)\s*\{\s*:\|:",
    r"\b(?:cat|less|more|head|tail|type)\b[^\n;|&]*(?:\.env\b|id_rsa\b|id_ed25519\b|credentials\.json\b)",
    r"\bGet-Content\b[^\n;|&]*(?:\.env\b|id_rsa\b|id_ed25519\b|credentials\.json\b)",
]


OutputCallback = Callable[[str, str], None]


def run_shell_command(
    root: Path,
    command: ShellCommand,
    *,
    permission_profile: PermissionProfile | None = None,
    output_callback: OutputCallback | None = None,
) -> CommandResult:
    reason = dangerous_reason(command.cmd)
    returncode = 126
    if reason is None:
        reason = permission_denial_reason(command.cmd, permission_profile)
        returncode = 0
    if reason:
        return CommandResult(
            cmd=command.cmd,
            returncode=returncode,
            stdout="",
            stderr="",
            skipped=True,
            blocked_reason=reason,
        )

    started = time.monotonic()
    if output_callback is not None:
        return _run_shell_command_streaming(root, command, started=started, output_callback=output_callback)

    try:
        completed = subprocess.run(
            command.cmd,
            cwd=root,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=command.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        return CommandResult(
            cmd=command.cmd,
            returncode=124,
            stdout=_truncate(exc.stdout or ""),
            stderr=_truncate(exc.stderr or f"Command timed out after {command.timeout}s."),
            duration_seconds=duration,
        )

    duration = time.monotonic() - started
    return CommandResult(
        cmd=command.cmd,
        returncode=completed.returncode,
        stdout=_truncate(completed.stdout),
        stderr=_truncate(completed.stderr),
        duration_seconds=duration,
    )


def _run_shell_command_streaming(
    root: Path,
    command: ShellCommand,
    *,
    started: float,
    output_callback: OutputCallback,
) -> CommandResult:
    try:
        process = subprocess.Popen(
            command.cmd,
            cwd=root,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
    except OSError as exc:
        return CommandResult(
            cmd=command.cmd,
            returncode=127,
            stdout="",
            stderr=str(exc),
            duration_seconds=time.monotonic() - started,
        )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_thread = threading.Thread(
        target=_read_pipe,
        args=("stdout", process.stdout, stdout_chunks, output_callback),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_read_pipe,
        args=("stderr", process.stderr, stderr_chunks, output_callback),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        returncode = process.wait(timeout=command.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        returncode = 124
        process.wait()

    stdout_thread.join(timeout=1)
    stderr_thread.join(timeout=1)
    duration = time.monotonic() - started
    stdout = _truncate("".join(stdout_chunks))
    stderr = _truncate("".join(stderr_chunks))
    if timed_out:
        timeout_message = f"Command timed out after {command.timeout}s."
        if stderr:
            stderr = _truncate(stderr + "\n" + timeout_message)
        else:
            stderr = timeout_message
            _safe_output_callback(output_callback, "stderr", stderr + "\n")

    return CommandResult(
        cmd=command.cmd,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
    )


def _read_pipe(
    stream_name: str,
    pipe,
    chunks: list[str],
    output_callback: OutputCallback,
) -> None:
    if pipe is None:
        return
    with pipe:
        for chunk in pipe:
            chunks.append(chunk)
            _safe_output_callback(output_callback, stream_name, chunk)


def _safe_output_callback(output_callback: OutputCallback, stream_name: str, text: str) -> None:
    try:
        output_callback(stream_name, text)
    except Exception:
        return


def dangerous_reason(cmd: str) -> str | None:
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd, flags=re.IGNORECASE):
            return f"Blocked dangerous command pattern: {pattern}"
    return None


def _truncate(text: str, limit: int = 12_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... output truncated ..."
