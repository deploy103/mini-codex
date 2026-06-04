from pathlib import Path

from mini_codex.config import DEFAULT_REQUEST_TIMEOUT, load_config, load_redaction_values


def test_load_config_supports_apim_env_names(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_MODE", raising=False)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "APIM_BASE_URL=https://gateway.example.test",
                "APIM_KEY=test-key",
                "CHAT_MODEL=test-chat-model",
                "APIM_TIMEOUT=45",
                "EMBEDDING_MODEL=test-embedding-model",
                "VISION_MODEL=test-vision-model",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.api_key == "test-key"
    assert config.base_url == "https://gateway.example.test/test-chat-model/"
    assert config.model == "test-chat-model"
    assert config.api_key_header == "api-key"
    assert config.api_mode == "auto"
    assert config.azure_api_version == "2024-10-21"
    assert config.request_timeout == 45


def test_openai_native_env_names_win(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("OPENAI_API_MODE", "chat")

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "APIM_BASE_URL=https://gateway.example.test",
                "APIM_KEY=apim-key",
                "CHAT_MODEL=chat-model",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.api_key == "openai-key"
    assert config.base_url == "https://api.example.test/v1"
    assert config.model == "openai-model"
    assert config.api_mode == "chat"


def test_empty_dotenv_values_do_not_mask_shell_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APIM_KEY", "shell-key")
    monkeypatch.setenv("APIM_BASE_URL", "https://gateway.example.test")
    monkeypatch.setenv("CHAT_MODEL", "shell-model")

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "APIM_KEY=",
                "APIM_BASE_URL=",
                "CHAT_MODEL=",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config.api_key == "shell-key"
    assert config.base_url == "https://gateway.example.test/shell-model/"
    assert config.model == "shell-model"


def test_load_redaction_values_collects_secret_env_values(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CUSTOM_PASSWORD", "shell-password")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "APIM_KEY=file-secret",
                "GITHUB_TOKEN=github-secret",
                "NORMAL_VALUE=public",
            ]
        ),
        encoding="utf-8",
    )

    values = load_redaction_values(tmp_path)

    assert "file-secret" in values
    assert "github-secret" in values
    assert "shell-password" in values
    assert "public" not in values


def test_load_config_does_not_leak_previous_dotenv_values(tmp_path: Path, monkeypatch):
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_API_MODE",
        "OPENAI_TIMEOUT",
        "APIM_KEY",
        "APIM_BASE_URL",
        "CHAT_MODEL",
        "APIM_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / ".env").write_text(
        "\n".join(
            [
                "APIM_BASE_URL=https://gateway.example.test",
                "APIM_KEY=first-key",
                "CHAT_MODEL=first-model",
                "APIM_TIMEOUT=45",
            ]
        ),
        encoding="utf-8",
    )
    (second / ".env").write_text(
        "\n".join(
            [
                "APIM_BASE_URL=https://gateway.example.test",
                "APIM_KEY=second-key",
                "CHAT_MODEL=second-model",
            ]
        ),
        encoding="utf-8",
    )

    first_config = load_config(first)
    second_config = load_config(second)

    assert first_config.request_timeout == 45
    assert second_config.request_timeout == DEFAULT_REQUEST_TIMEOUT
    assert second_config.model == "second-model"
