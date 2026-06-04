from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitState:
    command: str
    is_repo: bool
    returncode: int
    output: str


def inspect_git_state(workspace: Path) -> GitState:
    command = "git status --short --branch"
    completed = subprocess.run(
        command.split(),
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    output = (completed.stdout or completed.stderr).strip()
    is_repo = completed.returncode == 0
    if not output and is_repo:
        output = "clean"
    if not is_repo and "not a git repository" in output.lower():
        output = "not a git repository"
    return GitState(command=command, is_repo=is_repo, returncode=completed.returncode, output=output)
