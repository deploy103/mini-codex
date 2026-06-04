import shlex
import sys
from pathlib import Path

from mini_codex.commands import run_shell_command
from mini_codex.models import ShellCommand
from mini_codex.permissions import PermissionProfile


def test_run_shell_command_records_duration_and_output(tmp_path: Path):
    cmd = f"{shlex.quote(sys.executable)} -c \"print('ok')\""
    result = run_shell_command(tmp_path, ShellCommand(cmd=cmd, timeout=30))

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"
    assert result.duration_seconds >= 0


def test_run_shell_command_streams_output(tmp_path: Path):
    cmd = f"{shlex.quote(sys.executable)} -u -c \"print('live')\""
    chunks: list[tuple[str, str]] = []

    result = run_shell_command(
        tmp_path,
        ShellCommand(cmd=cmd, timeout=30),
        output_callback=lambda stream_name, text: chunks.append((stream_name, text)),
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "live"
    assert ("stdout", "live\n") in chunks


def test_run_shell_command_honors_permission_profile(tmp_path: Path):
    cmd = f"{shlex.quote(sys.executable)} -c \"print('blocked')\""
    profile = PermissionProfile(name="readonly", run_shell=False)

    result = run_shell_command(tmp_path, ShellCommand(cmd=cmd, timeout=30), permission_profile=profile)

    assert result.returncode == 0
    assert result.skipped is True
    assert "blocks shell commands" in (result.blocked_reason or "")


def test_run_shell_command_fails_dangerous_command_block(tmp_path: Path):
    result = run_shell_command(tmp_path, ShellCommand(cmd="git reset --hard", timeout=30))

    assert result.returncode == 126
    assert result.skipped is True
    assert "dangerous command pattern" in (result.blocked_reason or "")


def test_run_shell_command_blocks_rm_force_recursive_flag_order(tmp_path: Path):
    for cmd in ("rm -fr .", "rm -r -f build", "rm --recursive --force node_modules"):
        result = run_shell_command(tmp_path, ShellCommand(cmd=cmd, timeout=30))

        assert result.returncode == 126
        assert result.skipped is True
        assert "dangerous command pattern" in (result.blocked_reason or "")


def test_run_shell_command_blocks_sensitive_file_output(tmp_path: Path):
    for cmd in ("cat .env", "tail -n 5 id_rsa", "Get-Content credentials.json"):
        result = run_shell_command(tmp_path, ShellCommand(cmd=cmd, timeout=30))

        assert result.returncode == 126
        assert result.skipped is True
        assert "dangerous command pattern" in (result.blocked_reason or "")
