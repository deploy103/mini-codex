from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Edit:
    path: str
    action: str
    content: str | None = None


@dataclass(frozen=True)
class ShellCommand:
    cmd: str
    why: str = ""
    timeout: int = 120


@dataclass(frozen=True)
class PlanStep:
    title: str
    detail: str = ""


@dataclass(frozen=True)
class Plan:
    summary: str
    steps: list[PlanStep] = field(default_factory=list)
    edits: list[Edit] = field(default_factory=list)
    commands: list[ShellCommand] = field(default_factory=list)
    done: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommandResult:
    cmd: str
    returncode: int
    stdout: str
    stderr: str
    skipped: bool = False
    blocked_reason: str | None = None
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    iterations: int
    message: str
