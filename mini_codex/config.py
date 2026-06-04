from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None


DEFAULT_MODEL = "gpt-5-codex"
DEFAULT_AZURE_API_VERSION = "2024-10-21"
DEFAULT_REQUEST_TIMEOUT = 180.0
ALLOWED_API_MODES = {"auto", "responses", "chat", "foundry_models", "azure_v1", "azure_chat"}
SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


@dataclass(frozen=True)
class Config:
    api_key: str
    model: str
    base_url: str | None
    api_key_header: str | None
    api_mode: str
    azure_api_version: str
    request_timeout: float


def load_config(workspace: Path) -> Config:
    env_path = workspace / ".env"
    file_env = _dotenv_file_values(env_path)

    openai_api_key = _env("OPENAI_API_KEY", file_env)
    apim_key = _env("APIM_KEY", file_env)
    openai_base_url = _env("OPENAI_BASE_URL", file_env)
    apim_base_url = _env("APIM_BASE_URL", file_env)

    api_key = openai_api_key or apim_key or ""
    model = _env("OPENAI_MODEL", file_env) or _env("CHAT_MODEL", file_env) or DEFAULT_MODEL
    base_url = openai_base_url or _apim_model_base_url(apim_base_url, model) or None
    api_key_header = (
        _env("OPENAI_API_KEY_HEADER", file_env)
        or _env("APIM_KEY_HEADER", file_env)
        or ("api-key" if apim_key and apim_base_url and not openai_base_url else None)
    )
    api_mode = validate_api_mode(_env("OPENAI_API_MODE", file_env) or _env("APIM_API_MODE", file_env) or "auto")
    azure_api_version = (
        _env("AZURE_OPENAI_API_VERSION", file_env)
        or _env("OPENAI_API_VERSION", file_env)
        or _env("APIM_API_VERSION", file_env)
        or DEFAULT_AZURE_API_VERSION
    )
    request_timeout = _float_env(
        _env("OPENAI_TIMEOUT", file_env) or _env("APIM_TIMEOUT", file_env),
        default=DEFAULT_REQUEST_TIMEOUT,
        name="OPENAI_TIMEOUT/APIM_TIMEOUT",
    )

    if not api_key:
        raise RuntimeError(
            "Missing API key. Put APIM_KEY or OPENAI_API_KEY in .env, or export it in the shell."
        )

    return Config(
        api_key=api_key.strip(),
        model=model.strip(),
        base_url=base_url.strip() if base_url else None,
        api_key_header=api_key_header.strip() if api_key_header else None,
        api_mode=api_mode,
        azure_api_version=azure_api_version.strip(),
        request_timeout=request_timeout,
    )


def load_redaction_values(workspace: Path) -> tuple[str, ...]:
    file_env = _dotenv_file_values(workspace / ".env")
    values = set()
    for source in (file_env, os.environ):
        for name, value in source.items():
            if _looks_secret_env_name(name) and isinstance(value, str) and len(value.strip()) >= 4:
                values.add(value.strip())
    return tuple(sorted(values, key=len, reverse=True))


def _dotenv_file_values(env_path: Path) -> dict[str, str]:
    if dotenv_values is None or not env_path.exists():
        return {}
    values = dotenv_values(env_path)
    return {key: value for key, value in values.items() if isinstance(value, str)}


def _env(name: str, file_env: dict[str, str]) -> str | None:
    if name in file_env and file_env[name].strip():
        return file_env[name]
    return os.getenv(name)


def _looks_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in SECRET_ENV_MARKERS)


def _apim_model_base_url(apim_base_url: str | None, model: str) -> str | None:
    if not apim_base_url:
        return None
    base = apim_base_url.strip().rstrip("/")
    if not base:
        return None
    model = model.strip().strip("/")
    if model and not base.endswith("/" + model):
        base = f"{base}/{model}"
    return base + "/"


def validate_api_mode(api_mode: str) -> str:
    normalized = api_mode.strip().lower()
    if normalized not in ALLOWED_API_MODES:
        allowed = ", ".join(sorted(ALLOWED_API_MODES))
        raise RuntimeError(f"OPENAI_API_MODE/APIM_API_MODE must be one of: {allowed}.")
    return normalized


def _float_env(value: str | None, *, default: float, name: str) -> float:
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number of seconds.") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than zero.")
    return parsed
