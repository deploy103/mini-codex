from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

from .commands import run_shell_command
from .console import Console
from .llm import LLMClient
from .models import AgentResult, CommandResult
from .parser import PlanParseError, parse_plan
from .preflight import inspect_git_state
from .prompts import build_user_prompt
from .permissions import PermissionProfile
from .transcript import RunTranscript
from .workspace import apply_edits, build_workspace_context


@dataclass(frozen=True)
class AgentSettings:
    workspace: Path
    model: str
    api_key: str
    base_url: str | None
    api_key_header: str | None
    api_mode: str = "auto"
    azure_api_version: str = "2024-10-21"
    request_timeout: float = 180.0
    max_iterations: int = 3
    max_files: int = 80
    max_file_bytes: int = 20_000
    max_context_bytes: int = 180_000
    max_output_tokens: int = 12_000
    dry_run: bool = False
    run_commands: bool = True
    print_prompt: bool = False
    transcript: bool = True
    command_output_limit: int = 4_000
    permission_profile: PermissionProfile | None = None
    approval_mode: str = "never"
    initial_observations: tuple[str, ...] = ()


class CodingAgent:
    def __init__(self, settings: AgentSettings, *, console: Console | None = None) -> None:
        self.settings = settings
        self.console = console or Console()
        self.llm = LLMClient(
            api_key=settings.api_key,
            model=settings.model,
            base_url=settings.base_url,
            api_key_header=settings.api_key_header,
            api_mode=settings.api_mode,
            azure_api_version=settings.azure_api_version,
            request_timeout=settings.request_timeout,
            max_output_tokens=settings.max_output_tokens,
        )

    def run(self, task: str) -> AgentResult:
        workspace = self.settings.workspace.resolve()
        started = time.monotonic()
        observations: list[str] = list(self.settings.initial_observations)
        ok = False
        transcript = self._start_transcript(workspace, task)

        self.console.activity("Preparing local coding run")
        self.console.info(f"Workspace: {workspace}")
        self.console.info(f"Model: {self.settings.model}")
        self.console.info(f"API mode: {self.settings.api_mode}")
        self.console.info(f"Request timeout: {self.settings.request_timeout:g}s")
        self.console.info(f"Approval mode: {self.settings.approval_mode}")
        if self.settings.initial_observations:
            self.console.info(f"Resumed observations: {len(self.settings.initial_observations)}")
        if self.settings.permission_profile is not None:
            self.console.info(f"Permission profile: {self.settings.permission_profile.name}")
        if transcript is not None:
            self.console.info(f"Transcript: {transcript.path}")
            permission = (
                self.settings.permission_profile.name
                if self.settings.permission_profile is not None
                else "default dangerous-command blocklist"
            )
            transcript.activity(
                "Prepared workspace, model, API mode, request timeout, approval mode, "
                f"and permission profile ({self.settings.approval_mode}, {permission})."
            )
        if self.settings.dry_run:
            self.console.warn("Dry-run mode: no files will be written and no commands will run.")
        self._show_preflight(workspace, transcript=transcript)

        for iteration in range(1, self.settings.max_iterations + 1):
            self.console.rule(f"Iteration {iteration}")
            self.console.activity("Scanning workspace context")
            context = build_workspace_context(
                workspace,
                max_files=self.settings.max_files,
                max_file_bytes=self.settings.max_file_bytes,
                max_context_bytes=self.settings.max_context_bytes,
            )
            context_bytes = len(context.encode("utf-8"))
            context_files = _count_context_files(context)
            self.console.info(
                f"result: collected {context_bytes:,} bytes of workspace context from {context_files} files"
            )
            if transcript is not None:
                transcript.activity(
                    f"Scanned workspace context for the model ({context_bytes:,} bytes from {context_files} files)."
                )
            prompt = build_user_prompt(task, context, observations)
            if self.settings.print_prompt:
                self.console.rule("Prompt")
                self.console.info(prompt)

            self.console.activity("Asking the model for the next plan")
            raw = self.llm.create_plan(prompt)
            self.console.info(f"result: received {len(raw):,} characters from the model")
            try:
                plan = parse_plan(raw)
            except (PlanParseError, ValueError) as exc:
                safe_raw = self._redact_text(raw)
                observations.append(f"Model returned invalid JSON plan: {exc}\nRaw response:\n{safe_raw}")
                self.console.error(f"Invalid model plan: {exc}")
                if transcript is not None:
                    transcript.text("Invalid Model Plan", f"{exc}\n\n{safe_raw}")
                continue

            self._show_plan(plan, transcript=transcript)

            edit_observation = self._apply_plan_edits(workspace, plan.edits, transcript=transcript)
            if edit_observation:
                observations.append(edit_observation)

            command_results = self._run_plan_commands(workspace, plan.commands, transcript=transcript)
            if command_results:
                observations.append(_format_command_results(command_results))

            failed = any(result.returncode != 0 for result in command_results)
            if failed and iteration < self.settings.max_iterations:
                self.console.warn("A command failed; asking the model for a repair iteration.")
                continue

            if failed:
                return self._finish(
                    ok=False,
                    iterations=iteration,
                    message="A verification command failed.",
                    started=started,
                    transcript=transcript,
                )

            if plan.done:
                ok = True
                return self._finish(
                    ok=True,
                    iterations=iteration,
                    message=self._completion_message(),
                    started=started,
                    transcript=transcript,
                )

            if not plan.edits and not plan.commands:
                return self._finish(
                    ok=True,
                    iterations=iteration,
                    message="No further work returned.",
                    started=started,
                    transcript=transcript,
                )

        return self._finish(
            ok=ok,
            iterations=self.settings.max_iterations,
            message="Reached iteration limit.",
            started=started,
            transcript=transcript,
        )

    def _show_plan(self, plan, *, transcript: RunTranscript | None) -> None:
        self.console.activity("Plan received")
        self.console.info(self._redact_text(plan.summary) or "Model returned a plan.")
        if plan.steps:
            self.console.info("Planned activities:")
            for step in plan.steps:
                title = self._redact_text(step.title)
                detail = f" - {self._redact_text(step.detail)}" if step.detail else ""
                self.console.plan_item(f"{title}{detail}")

        if plan.edits:
            self.console.info("Files to change:")
            for edit in plan.edits:
                self.console.plan_item(f"{edit.action}: {edit.path}")
        else:
            self.console.info("Files to change: none")

        if plan.commands:
            self.console.info("Commands to run:")
            for command in plan.commands:
                why = f" ({self._redact_text(command.why)})" if command.why else ""
                self.console.plan_item(f"{self._redact_text(command.cmd)}{why}")
        else:
            self.console.info("Commands to run: none")

        if plan.notes:
            self.console.info("Notes:")
            for note in plan.notes:
                self.console.plan_item(self._redact_text(note))

        if transcript is not None:
            lines = [self._redact_text(plan.summary) or "Model returned a plan.", ""]
            lines.append("Activities:")
            lines.extend(
                f"- {self._redact_text(step.title)}: {self._redact_text(step.detail)}"
                if step.detail
                else f"- {self._redact_text(step.title)}"
                for step in plan.steps
            )
            if not plan.steps:
                lines.append("- none")
            lines.append("")
            lines.append("Files:")
            lines.extend(f"- {edit.action}: {edit.path}" for edit in plan.edits)
            if not plan.edits:
                lines.append("- none")
            lines.append("")
            lines.append("Commands:")
            lines.extend(
                f"- {self._redact_text(command.cmd)} ({self._redact_text(command.why) or 'no reason provided'})"
                for command in plan.commands
            )
            if not plan.commands:
                lines.append("- none")
            if plan.notes:
                lines.append("")
                lines.append("Notes:")
                lines.extend(f"- {self._redact_text(note)}" for note in plan.notes)
            transcript.text("Plan", "\n".join(lines))

    def _show_preflight(self, workspace: Path, *, transcript: RunTranscript | None) -> None:
        self.console.activity("Checking workspace state")
        try:
            git_state = inspect_git_state(workspace)
        except (OSError, subprocess.SubprocessError) as exc:
            self.console.warn(f"git status unavailable: {exc}")
            if transcript is not None:
                transcript.text("Workspace State", f"git status unavailable: {exc}")
            return

        self.console.command(git_state.command, why="inspect workspace state before editing", timeout=15)
        if git_state.is_repo:
            self.console.info("result: git repository")
            self.console.info(git_state.output)
        else:
            self.console.info(f"result: {git_state.output}")
        if transcript is not None:
            transcript.text(
                "Workspace State",
                "\n".join(
                    [
                        f"$ {git_state.command}",
                        f"exit code: {git_state.returncode}",
                        git_state.output,
                    ]
                ),
            )

    def _apply_plan_edits(self, workspace: Path, edits: list, *, transcript: RunTranscript | None) -> str:
        if not edits:
            self.console.info("No file edits.")
            if transcript is not None:
                transcript.activity("No file edits were requested.")
            return ""

        self.console.activity("Applying file edits")
        applied = apply_edits(workspace, edits, dry_run=self.settings.dry_run)
        lines = ["File edits:"]
        for edit in applied:
            status = "would change" if self.settings.dry_run and edit.changed else "changed" if edit.changed else "unchanged"
            delta = _format_edit_delta(edit.added_lines, edit.removed_lines)
            self.console.info(f"{edit.action}: {edit.path} ({status}{delta})")
            lines.append(f"- {edit.action} {edit.path}: {status}{delta}")
        changed = sum(1 for edit in applied if edit.changed)
        unchanged = len(applied) - changed
        added = sum(edit.added_lines for edit in applied)
        removed = sum(edit.removed_lines for edit in applied)
        self.console.info(f"result: {changed} changed, {unchanged} unchanged, +{added} -{removed}")
        if transcript is not None:
            transcript.text("File Edits", "\n".join(lines))
        return "\n".join(lines)

    def _run_plan_commands(
        self,
        workspace: Path,
        commands: list,
        *,
        transcript: RunTranscript | None,
    ) -> list[CommandResult]:
        if not commands:
            self.console.info("No shell commands.")
            if transcript is not None:
                transcript.activity("No shell commands were requested.")
            return []

        if self.settings.dry_run or not self.settings.run_commands:
            skipped_results: list[CommandResult] = []
            for command in commands:
                display_cmd = self._redact_text(command.cmd)
                display_why = self._redact_text(command.why)
                self.console.command(display_cmd, why=display_why, timeout=command.timeout)
                if transcript is not None:
                    transcript.command(cmd=display_cmd, why=display_why, timeout=command.timeout)
                self.console.command_result(
                    returncode=0,
                    stderr="Skipped by local runner settings.",
                    skipped=True,
                    duration_seconds=0.0,
                    output_limit=self.settings.command_output_limit,
                )
                result = CommandResult(
                    cmd=display_cmd,
                    returncode=0,
                    stdout="",
                    stderr="Skipped by local runner settings.",
                    skipped=True,
                    duration_seconds=0.0,
                )
                if transcript is not None:
                    transcript.command_result(result)
                skipped_results.append(result)
            self._show_command_summary(skipped_results, transcript=transcript)
            return skipped_results

        results: list[CommandResult] = []
        self.console.activity("Running shell commands")
        for command in commands:
            display_cmd = self._redact_text(command.cmd)
            display_why = self._redact_text(command.why)
            self.console.command(display_cmd, why=display_why, timeout=command.timeout)
            if transcript is not None:
                transcript.command(cmd=display_cmd, why=display_why, timeout=command.timeout)
            approval_denial = self._command_approval_denial(display_cmd, display_why)
            if approval_denial is not None:
                result = CommandResult(
                    cmd=display_cmd,
                    returncode=0,
                    stdout="",
                    stderr="",
                    skipped=True,
                    blocked_reason=approval_denial,
                )
                self.console.command_result(
                    returncode=result.returncode,
                    skipped=True,
                    blocked_reason=approval_denial,
                    duration_seconds=0.0,
                    output_limit=self.settings.command_output_limit,
                )
                if transcript is not None:
                    transcript.command_result(result)
                results.append(result)
                continue
            streamed_output = False
            streamed_counts = {"stdout": 0, "stderr": 0}
            streamed_truncated: set[str] = set()

            def output_callback(stream_name: str, text: str) -> None:
                nonlocal streamed_output
                streamed_output = True
                output = _limit_streamed_output(
                    self._redact_text(text),
                    stream_name=stream_name,
                    counts=streamed_counts,
                    truncated=streamed_truncated,
                    limit=self.settings.command_output_limit,
                )
                if output:
                    self.console.command_output(stream_name, output, output_limit=len(output))

            result = self._redact_command_result(
                run_shell_command(
                    workspace,
                    command,
                    permission_profile=self.settings.permission_profile,
                    output_callback=output_callback,
                )
            )
            self.console.command_result(
                returncode=result.returncode,
                stdout="" if streamed_output else result.stdout,
                stderr="" if streamed_output else result.stderr,
                skipped=result.skipped,
                blocked_reason=result.blocked_reason,
                duration_seconds=result.duration_seconds,
                output_limit=self.settings.command_output_limit,
            )
            if transcript is not None:
                transcript.command_result(result)
            results.append(result)
        self._show_command_summary(results, transcript=transcript)
        return results

    def _command_approval_denial(self, cmd: str, why: str) -> str | None:
        if self.settings.approval_mode == "never":
            return None
        if self.settings.approval_mode != "always":
            return f"Unknown approval mode: {self.settings.approval_mode}"
        if not sys.stdin.isatty():
            return "Approval required but stdin is not interactive."

        self.console.warn("Approval required before running this shell command.")
        if why:
            self.console.info(f"why: {why}")
        try:
            answer = input("Run command? [y/N] ")
        except EOFError:
            return "Command denied because no approval input was available."
        if answer.strip().lower() in {"y", "yes"}:
            return None
        return "Command denied by user."

    def _finish(
        self,
        *,
        ok: bool,
        iterations: int,
        message: str,
        started: float,
        transcript: RunTranscript | None,
    ) -> AgentResult:
        elapsed = time.monotonic() - started
        if ok:
            self.console.activity(f"Finished: {message}")
        else:
            self.console.error(f"Finished: {message}")
        self.console.info(f"Iterations: {iterations}")
        self.console.info(f"Elapsed: {elapsed:.2f}s")
        if transcript is not None:
            self.console.info(f"Transcript saved: {transcript.path}")
        if transcript is not None:
            transcript.final(ok=ok, iterations=iterations, message=message, elapsed_seconds=elapsed)
        return AgentResult(ok=ok, iterations=iterations, message=message)

    def _start_transcript(self, workspace: Path, task: str) -> RunTranscript | None:
        if not self.settings.transcript:
            return None
        try:
            return RunTranscript(workspace, task=task)
        except OSError as exc:
            self.console.warn(f"Transcript disabled: {exc}")
            return None

    def _completion_message(self) -> str:
        if self.settings.dry_run:
            return "Dry run completed; no files were written and no commands were run."
        if not self.settings.run_commands:
            return "Task completed with shell commands skipped."
        return "Task completed."

    def _show_command_summary(
        self,
        results: list[CommandResult],
        *,
        transcript: RunTranscript | None,
    ) -> None:
        passed = sum(1 for result in results if not result.skipped and result.returncode == 0)
        failed = sum(1 for result in results if not result.skipped and result.returncode != 0)
        skipped = sum(1 for result in results if result.skipped)
        summary = f"result: {passed} passed, {failed} failed, {skipped} skipped"
        self.console.info(summary)
        if transcript is not None:
            transcript.text("Command Summary", summary)

    def _redact_command_result(self, result: CommandResult) -> CommandResult:
        return replace(
            result,
            cmd=self._redact_text(result.cmd),
            stdout=self._redact_text(result.stdout),
            stderr=self._redact_text(result.stderr),
            blocked_reason=self._redact_text(result.blocked_reason) if result.blocked_reason else None,
        )

    def _redact_text(self, text: str) -> str:
        redacted = text
        for secret in self._redaction_values():
            redacted = redacted.replace(secret, "[redacted]")
        return redacted

    def _redaction_values(self) -> list[str]:
        secrets = {
            value.strip()
            for value in (self.settings.api_key,)
            if isinstance(value, str) and len(value.strip()) >= 4
        }
        return sorted(secrets, key=len, reverse=True)


def _format_command_results(results: list[CommandResult]) -> str:
    chunks = ["Command results:"]
    for result in results:
        chunks.append(f"$ {result.cmd}")
        if result.skipped:
            chunks.append(f"SKIPPED: {result.blocked_reason or result.stderr}")
            continue
        chunks.append(f"exit code: {result.returncode}")
        chunks.append(f"duration: {result.duration_seconds:.2f}s")
        if result.stdout:
            chunks.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            chunks.append(f"stderr:\n{result.stderr}")
    return "\n".join(chunks)


def _format_edit_delta(added: int, removed: int) -> str:
    if added == 0 and removed == 0:
        return ""
    return f", +{added} -{removed}"


def _count_context_files(context: str) -> int:
    lines = context.splitlines()
    if not lines or lines[0] != "Tree:":
        return 0
    count = 0
    for line in lines[1:]:
        if not line:
            break
        if line == "(empty workspace)":
            return 0
        count += 1
    return count


def _limit_streamed_output(
    text: str,
    *,
    stream_name: str,
    counts: dict[str, int],
    truncated: set[str],
    limit: int,
) -> str:
    if limit <= 0:
        return ""
    used = counts.get(stream_name, 0)
    remaining = limit - used
    if remaining <= 0:
        if stream_name in truncated:
            return ""
        truncated.add(stream_name)
        return "... output truncated ...\n"
    counts[stream_name] = used + min(len(text), remaining)
    if len(text) <= remaining:
        return text
    truncated.add(stream_name)
    return text[:remaining] + "\n... output truncated ...\n"
