from __future__ import annotations

import json
import re
from typing import Any

from .models import Edit, Plan, PlanStep, ShellCommand


class PlanParseError(ValueError):
    pass


def parse_plan(text: str) -> Plan:
    payload = _extract_json(text)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise PlanParseError("Model response must be a JSON object.")

    summary = _string(data.get("summary"), "summary", default="")
    steps = [_parse_step(item) for item in _list(data.get("steps"), "steps")]
    edits = [_parse_edit(item) for item in _list(data.get("edits"), "edits")]
    commands = [_parse_command(item) for item in _list(data.get("commands"), "commands")]
    notes = [_string(item, "notes item", default="") for item in _list(data.get("notes"), "notes")]

    return Plan(
        summary=summary,
        steps=steps,
        edits=edits,
        commands=commands,
        done=bool(data.get("done", False)),
        notes=[note for note in notes if note],
    )


def _parse_step(item: Any) -> PlanStep:
    if isinstance(item, str):
        return PlanStep(title=item)
    if not isinstance(item, dict):
        raise PlanParseError("Each step must be a string or object.")
    return PlanStep(
        title=_string(item.get("title"), "step.title"),
        detail=_string(item.get("detail"), "step.detail", default=""),
    )


def _parse_edit(item: Any) -> Edit:
    if not isinstance(item, dict):
        raise PlanParseError("Each edit must be an object.")
    action = _string(item.get("action"), "edit.action").lower()
    if action not in {"create", "update", "delete", "patch"}:
        raise PlanParseError(f"Unsupported edit action: {action}")
    content = item.get("content")
    if action in {"create", "update", "patch"} and not isinstance(content, str):
        raise PlanParseError(f"Edit for {item.get('path')} needs string content.")
    return Edit(
        path=_string(item.get("path"), "edit.path"),
        action=action,
        content=content if isinstance(content, str) else None,
    )


def _parse_command(item: Any) -> ShellCommand:
    if not isinstance(item, dict):
        raise PlanParseError("Each command must be an object.")
    timeout = item.get("timeout", 120)
    if not isinstance(timeout, int):
        timeout = 120
    timeout = max(1, min(timeout, 600))
    return ShellCommand(
        cmd=_string(item.get("cmd"), "command.cmd"),
        why=_string(item.get("why"), "command.why", default=""),
        timeout=timeout,
    )


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if fenced:
        return fenced.group(1)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]

    raise PlanParseError("Could not find a JSON object in the model response.")


def _string(value: Any, name: str, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str):
        raise PlanParseError(f"{name} must be a string.")
    return value


def _list(value: Any, name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise PlanParseError(f"{name} must be a list.")
    return value
