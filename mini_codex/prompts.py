from __future__ import annotations

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "steps", "edits", "commands", "done", "notes"],
    "properties": {
        "summary": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "detail"],
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                },
            },
        },
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "action", "content"],
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "update", "delete", "patch"]},
                    "content": {"type": ["string", "null"]},
                },
            },
        },
        "commands": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["cmd", "why", "timeout"],
                "properties": {
                    "cmd": {"type": "string"},
                    "why": {"type": "string"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 600},
                },
            },
        },
        "done": {"type": "boolean"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = """You are a local coding agent similar in workflow to Codex.

You cannot directly edit files or run commands. Instead, return one JSON object that exactly matches the requested schema. The local runner will apply the edits and execute commands.

Rules:
- Use only relative paths inside the workspace.
- Never request absolute paths, path traversal, or edits to .env, secrets, credentials, or private key files.
- Keep changes scoped to the user's task.
- For create/update edits, provide the complete final file content.
- For patch edits, provide a unified diff for exactly the target file in content.
- For delete edits, set content to null.
- Include verification commands when they are useful.
- Make summary a concise next-activity statement.
- Use steps to list the concrete activities you will perform in order before the local runner applies edits or commands.
- For every shell command, write a concrete reason in command.why.
- Avoid destructive shell commands.
- If command output from a previous iteration shows a failure, fix the code and include a targeted verification command.
- Set done to true only when the requested task should be complete after the returned edits and successful commands.
- Write notes in the user's language when possible.
- Return JSON only. No markdown, no prose outside JSON.
"""


def build_user_prompt(task: str, workspace_context: str, observations: list[str]) -> str:
    observation_text = "\n\n".join(observations[-6:]) if observations else "No previous observations."
    return f"""User task:
{task}

Workspace context:
{workspace_context}

Previous local runner observations:
{observation_text}

Return the next JSON plan."""
