from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
BUILTIN_TEMPLATE_NAMES = {"readonly", "restricted", "trusted"}
SHELL_CONTROL_RE = re.compile(r"&&|\|\||\$\(|\$\{|[;|`<>]")

RESTRICTED_ALLOW_PATTERNS = (
    r"^(?:\.venv(?:-win)?[\\/](?:bin|Scripts)[\\/])?(?:python(?:3(?:\.\d+)?)?|py)(?:\.exe)?\s+-m\s+pytest\b",
    r"^pytest\b",
    r"^(?:npm|pnpm|yarn)\s+(?:test|run\s+(?:test|lint|build|typecheck))\b",
    r"^ruff\s+(?:check|format\s+--check)\b",
    r"^mypy\b",
    r"^tsc\s+(?:--noEmit|-p\b)",
    r"^git\s+(?:status|diff|show|log|rev-parse)\b",
)


@dataclass(frozen=True)
class PermissionProfile:
    name: str
    description: str = ""
    run_shell: bool = True
    allow_patterns: tuple[str, ...] = ()
    deny_patterns: tuple[str, ...] = ()
    builtin: bool = False


def builtin_permission_profile(name: str) -> PermissionProfile:
    normalized = normalize_profile_name(name).lower()
    if normalized == "readonly":
        return PermissionProfile(
            name="readonly",
            description="Do not run shell commands.",
            run_shell=False,
            builtin=True,
        )
    if normalized == "restricted":
        return PermissionProfile(
            name="restricted",
            description="Allow common verification commands only.",
            run_shell=True,
            allow_patterns=RESTRICTED_ALLOW_PATTERNS,
            builtin=True,
        )
    if normalized == "trusted":
        return PermissionProfile(
            name="trusted",
            description="Allow shell commands except built-in dangerous command blocks.",
            run_shell=True,
            builtin=True,
        )
    raise ValueError(f"Unknown permission template: {name}")


def create_permission_profile(
    name: str,
    *,
    template: str = "restricted",
    description: str = "",
    allow_patterns: list[str] | None = None,
    deny_patterns: list[str] | None = None,
    run_shell: bool | None = None,
) -> PermissionProfile:
    normalized = normalize_profile_name(name)
    if normalized.lower() in BUILTIN_TEMPLATE_NAMES:
        raise ValueError(f"{normalized!r} is reserved for a built-in permission profile.")
    base = builtin_permission_profile(template)
    profile = PermissionProfile(
        name=normalized,
        description=description or f"Custom profile based on {base.name}.",
        run_shell=base.run_shell if run_shell is None else run_shell,
        allow_patterns=tuple(allow_patterns if allow_patterns is not None else base.allow_patterns),
        deny_patterns=tuple(deny_patterns if deny_patterns is not None else base.deny_patterns),
        builtin=False,
    )
    validate_permission_profile(profile)
    return profile


def load_permission_profile(workspace: Path, name: str) -> PermissionProfile:
    normalized = normalize_profile_name(name)
    builtin_name = normalized.lower()
    if builtin_name in BUILTIN_TEMPLATE_NAMES:
        return builtin_permission_profile(builtin_name)
    path = permission_profile_path(workspace, normalized)
    if path.exists():
        profile = permission_profile_from_json(path.read_text(encoding="utf-8"))
        if profile.name != normalized:
            raise ValueError(f"Permission profile file name and profile name differ: {normalized}")
        return profile
    raise FileNotFoundError(f"Permission profile not found: {normalized}")


def save_permission_profile(workspace: Path, profile: PermissionProfile, *, force: bool = False) -> Path:
    validate_permission_profile(profile)
    if profile.builtin:
        raise ValueError("Built-in permission profiles cannot be saved.")
    if profile.name.lower() in BUILTIN_TEMPLATE_NAMES:
        raise ValueError(f"{profile.name!r} is reserved for a built-in permission profile.")
    path = permission_profile_path(workspace, profile.name)
    if path.exists() and not force:
        raise FileExistsError(f"Permission profile already exists: {profile.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(permission_profile_to_json(profile), encoding="utf-8", newline="")
    return path


def delete_permission_profile(workspace: Path, name: str) -> Path:
    normalized = normalize_profile_name(name)
    if normalized.lower() in BUILTIN_TEMPLATE_NAMES:
        raise ValueError(f"Built-in permission profile cannot be deleted: {normalized}")
    path = permission_profile_path(workspace, normalized)
    if not path.exists():
        raise FileNotFoundError(f"Permission profile not found: {normalized}")
    path.unlink()
    return path


def list_permission_profiles(workspace: Path) -> list[PermissionProfile]:
    profiles = [builtin_permission_profile(name) for name in sorted(BUILTIN_TEMPLATE_NAMES)]
    custom_dir = permission_profiles_dir(workspace)
    if not custom_dir.exists():
        return profiles
    for path in sorted(custom_dir.glob("*.json")):
        try:
            profiles.append(permission_profile_from_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return profiles


def permission_profiles_dir(workspace: Path) -> Path:
    return workspace.resolve() / ".mini_codex" / "permissions"


def permission_profile_path(workspace: Path, name: str) -> Path:
    normalized = normalize_profile_name(name)
    return permission_profiles_dir(workspace) / f"{normalized}.json"


def normalize_profile_name(name: str) -> str:
    normalized = name.strip()
    if not PROFILE_NAME_RE.fullmatch(normalized):
        raise ValueError("Permission profile names may contain only letters, numbers, dots, dashes, and underscores.")
    return normalized


def validate_permission_profile(profile: PermissionProfile) -> None:
    normalize_profile_name(profile.name)
    for pattern in (*profile.allow_patterns, *profile.deny_patterns):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid permission regex pattern {pattern!r}: {exc}") from exc


def permission_denial_reason(command: str, profile: PermissionProfile | None) -> str | None:
    if profile is None:
        return None
    if not profile.run_shell:
        return f"Permission profile '{profile.name}' blocks shell commands."
    deny_pattern = _first_matching_pattern(profile.deny_patterns, command)
    if deny_pattern is not None:
        return f"Blocked by permission profile '{profile.name}' deny pattern: {deny_pattern}"
    if profile.allow_patterns and SHELL_CONTROL_RE.search(command):
        return f"Blocked by permission profile '{profile.name}': shell control operators are not allowed."
    if profile.allow_patterns and _first_matching_pattern(profile.allow_patterns, command) is None:
        return f"Blocked by permission profile '{profile.name}': command does not match allowed patterns."
    return None


def permission_profile_to_json(profile: PermissionProfile) -> str:
    validate_permission_profile(profile)
    payload = {
        "name": profile.name,
        "description": profile.description,
        "run_shell": profile.run_shell,
        "allow_patterns": list(profile.allow_patterns),
        "deny_patterns": list(profile.deny_patterns),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def permission_profile_from_json(text: str) -> PermissionProfile:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Permission profile must be a JSON object.")
    profile = PermissionProfile(
        name=_required_string(data, "name"),
        description=_optional_string(data, "description"),
        run_shell=_optional_bool(data, "run_shell", default=True),
        allow_patterns=tuple(_string_list(data, "allow_patterns")),
        deny_patterns=tuple(_string_list(data, "deny_patterns")),
        builtin=False,
    )
    validate_permission_profile(profile)
    return profile


def _first_matching_pattern(patterns: tuple[str, ...], command: str) -> str | None:
    for pattern in patterns:
        if re.search(pattern, command):
            return pattern
    return None


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"Permission profile field {key!r} must be a string.")
    return value


def _optional_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"Permission profile field {key!r} must be a string.")
    return value


def _optional_bool(data: dict[str, Any], key: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"Permission profile field {key!r} must be a boolean.")
    return value


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Permission profile field {key!r} must be a list of strings.")
    return value
