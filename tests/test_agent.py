import io
import json
import shlex
import sys
from pathlib import Path

from mini_codex.agent import AgentSettings, CodingAgent
from mini_codex.console import Console
from mini_codex.permissions import builtin_permission_profile


class FakeLLM:
    def create_plan(self, user_prompt: str) -> str:
        return json.dumps(
            {
                "summary": "demo plan",
                "steps": [{"title": "prepare demo", "detail": "create a sample file"}],
                "edits": [{"path": "demo.txt", "action": "create", "content": "hello\n"}],
                "commands": [{"cmd": "python -c \"print('ok')\"", "why": "verify", "timeout": 5}],
                "done": True,
                "notes": ["test note"],
            }
        )


class SecretEchoLLM:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def create_plan(self, user_prompt: str) -> str:
        cmd = f"{shlex.quote(sys.executable)} -c \"print('{self.secret}')\""
        return json.dumps(
            {
                "summary": f"echo {self.secret}",
                "steps": [{"title": "run secret echo", "detail": self.secret}],
                "edits": [],
                "commands": [{"cmd": cmd, "why": f"verify redaction {self.secret}", "timeout": 30}],
                "done": True,
                "notes": [self.secret],
            }
        )


class StreamingOutputLLM:
    def create_plan(self, user_prompt: str) -> str:
        cmd = (
            f"{shlex.quote(sys.executable)} -u -c "
            "\"print('12345678'); print('abcdefgh')\""
        )
        return json.dumps(
            {
                "summary": "stream output",
                "steps": [{"title": "run stream", "detail": "emit multiple chunks"}],
                "edits": [],
                "commands": [{"cmd": cmd, "why": "verify stream limit", "timeout": 30}],
                "done": True,
                "notes": [],
            }
        )


class PromptCaptureLLM:
    def __init__(self) -> None:
        self.prompt = ""

    def create_plan(self, user_prompt: str) -> str:
        self.prompt = user_prompt
        return json.dumps(
            {
                "summary": "done",
                "steps": [],
                "edits": [],
                "commands": [],
                "done": True,
                "notes": [],
            }
        )


def test_agent_prints_activity_command_and_result_for_dry_run(tmp_path: Path, capsys):
    agent = CodingAgent.__new__(CodingAgent)
    agent.settings = AgentSettings(
        workspace=tmp_path,
        model="fake-model",
        api_key="fake-key",
        base_url=None,
        api_key_header=None,
        dry_run=True,
    )
    agent.console = Console()
    agent.llm = FakeLLM()

    result = agent.run("make a demo")

    captured = capsys.readouterr()
    assert result.ok is True
    assert result.message.startswith("Dry run completed")
    assert "Preparing local coding run" in captured.out
    assert "Checking workspace state" in captured.out
    assert "$ git status --short --branch" in captured.out
    assert "Plan received" in captured.out
    assert "Planned activities:" in captured.out
    assert "prepare demo" in captured.out
    assert "+1 -0" in captured.out
    assert "$ python -c" in captured.out
    assert "skipped" in captured.out
    assert "0 passed, 0 failed, 1 skipped" in captured.out


def test_agent_limits_streaming_output_per_stream(tmp_path: Path, capsys):
    agent = CodingAgent.__new__(CodingAgent)
    agent.settings = AgentSettings(
        workspace=tmp_path,
        model="fake-model",
        api_key="fake-key",
        base_url=None,
        api_key_header=None,
        command_output_limit=10,
    )
    agent.console = Console()
    agent.llm = StreamingOutputLLM()

    result = agent.run("stream output")

    captured = capsys.readouterr()
    assert result.ok is True
    assert "stdout: 12345678" in captured.out
    assert "stdout: abcdefgh" not in captured.out
    assert "... output truncated ..." in captured.out
    assert "Elapsed:" in captured.out
    assert "Transcript saved:" in captured.out
    assert not (tmp_path / "demo.txt").exists()
    assert list((tmp_path / ".mini_codex" / "runs").glob("*.md"))


def test_agent_redacts_api_key_from_plan_output_command_output_and_transcript(tmp_path: Path, capsys):
    secret = "secret-token-123"
    agent = CodingAgent.__new__(CodingAgent)
    agent.settings = AgentSettings(
        workspace=tmp_path,
        model="fake-model",
        api_key=secret,
        base_url=None,
        api_key_header=None,
    )
    agent.console = Console()
    agent.llm = SecretEchoLLM(secret)

    result = agent.run("check redaction")

    captured = capsys.readouterr()
    transcript_paths = list((tmp_path / ".mini_codex" / "runs").glob("*.md"))
    transcript = transcript_paths[0].read_text(encoding="utf-8")
    assert result.ok is True
    assert secret not in captured.out
    assert secret not in captured.err
    assert secret not in transcript
    assert "[redacted]" in captured.out
    assert "[redacted]" in transcript


def test_agent_redacts_additional_secret_values(tmp_path: Path, capsys):
    secret = "secondary-secret-123"
    agent = CodingAgent.__new__(CodingAgent)
    agent.settings = AgentSettings(
        workspace=tmp_path,
        model="fake-model",
        api_key="fake-key",
        base_url=None,
        api_key_header=None,
        redaction_values=(secret,),
    )
    agent.console = Console()
    agent.llm = SecretEchoLLM(secret)

    result = agent.run("check redaction")

    captured = capsys.readouterr()
    transcript = next((tmp_path / ".mini_codex" / "runs").glob("*.md")).read_text(encoding="utf-8")
    assert result.ok is True
    assert secret not in captured.out
    assert secret not in transcript
    assert "[redacted]" in captured.out


def test_agent_treats_permission_blocked_commands_as_skipped(tmp_path: Path, capsys):
    agent = CodingAgent.__new__(CodingAgent)
    agent.settings = AgentSettings(
        workspace=tmp_path,
        model="fake-model",
        api_key="fake-key",
        base_url=None,
        api_key_header=None,
        permission_profile=builtin_permission_profile("readonly"),
    )
    agent.console = Console()
    agent.llm = FakeLLM()

    result = agent.run("make a demo")

    captured = capsys.readouterr()
    assert result.ok is True
    assert "Permission profile: readonly" in captured.out
    assert "skipped" in captured.out
    assert "0 passed, 0 failed, 1 skipped" in captured.out


def test_agent_approval_mode_skips_commands_without_interactive_stdin(tmp_path: Path, capsys):
    agent = CodingAgent.__new__(CodingAgent)
    agent.settings = AgentSettings(
        workspace=tmp_path,
        model="fake-model",
        api_key="fake-key",
        base_url=None,
        api_key_header=None,
        approval_mode="always",
    )
    agent.console = Console()
    agent.llm = FakeLLM()

    result = agent.run("make a demo")

    captured = capsys.readouterr()
    assert result.ok is True
    assert "Approval mode: always" in captured.out
    assert "Approval required but stdin is not interactive" in captured.out
    assert "0 passed, 0 failed, 1 skipped" in captured.out


def test_agent_approval_mode_runs_command_after_yes(tmp_path: Path, monkeypatch, capsys):
    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    agent = CodingAgent.__new__(CodingAgent)
    agent.settings = AgentSettings(
        workspace=tmp_path,
        model="fake-model",
        api_key="fake-key",
        base_url=None,
        api_key_header=None,
        approval_mode="always",
    )
    agent.console = Console()
    agent.llm = StreamingOutputLLM()
    monkeypatch.setattr(sys, "stdin", TtyInput("y\n"))

    result = agent.run("stream output")

    captured = capsys.readouterr()
    assert result.ok is True
    assert "Approval required before running this shell command" in captured.out
    assert "stdout: 12345678" in captured.out
    assert "1 passed, 0 failed, 0 skipped" in captured.out


def test_agent_includes_initial_observations_in_prompt(tmp_path: Path):
    agent = CodingAgent.__new__(CodingAgent)
    agent.settings = AgentSettings(
        workspace=tmp_path,
        model="fake-model",
        api_key="fake-key",
        base_url=None,
        api_key_header=None,
        initial_observations=("Previous run transcript:\nold failure",),
    )
    agent.console = Console()
    llm = PromptCaptureLLM()
    agent.llm = llm

    result = agent.run("continue")

    assert result.ok is True
    assert "Previous run transcript:" in llm.prompt
    assert "old failure" in llm.prompt
