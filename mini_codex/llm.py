from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .prompts import PLAN_SCHEMA, SYSTEM_PROMPT


class LLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        api_key_header: str | None = None,
        api_mode: str = "auto",
        azure_api_version: str = "2024-10-21",
        request_timeout: float = 180.0,
        max_output_tokens: int = 12_000,
    ) -> None:
        try:
            from openai import AzureOpenAI, OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install dependencies first: pip install -e .") from exc

        default_headers: dict[str, str] = {}
        client_api_key = api_key
        if api_key_header:
            default_headers[api_key_header] = api_key
            client_api_key = "placeholder"

        self._client = OpenAI(
            api_key=client_api_key,
            base_url=base_url,
            default_headers=default_headers or None,
            timeout=request_timeout,
        )
        self._OpenAI = OpenAI
        self._AzureOpenAI = AzureOpenAI
        self._api_key = api_key
        self._base_url = base_url
        self._api_key_header = api_key_header
        self._model = model
        self._api_mode = api_mode
        self._azure_api_version = azure_api_version
        self._request_timeout = request_timeout
        self._max_output_tokens = max_output_tokens

    def create_plan(self, user_prompt: str) -> str:
        if self._api_mode == "azure_v1":
            return self._create_azure_v1_chat_plan(user_prompt)

        if self._api_mode == "azure_chat":
            return self._create_azure_deployment_chat_plan(user_prompt)

        if self._api_mode == "foundry_models":
            return self._create_foundry_models_chat_plan(user_prompt)

        if self._api_mode == "chat":
            return self._create_chat_plan(user_prompt)

        if self._api_mode == "responses":
            return self._create_responses_plan(user_prompt)

        candidates = [
            ("responses", self._create_responses_plan),
            ("chat", self._create_chat_plan),
            ("foundry_models", self._create_foundry_models_chat_plan),
            ("azure_v1_responses", self._create_azure_v1_responses_plan),
            ("azure_v1_chat", self._create_azure_v1_chat_plan),
            ("azure_chat", self._create_azure_deployment_chat_plan),
        ]
        last_error: Exception | None = None
        fallback_errors: list[str] = []
        for label, candidate in candidates:
            try:
                return candidate(user_prompt)
            except Exception as exc:
                last_error = exc
                if not _should_try_next_endpoint(exc):
                    raise
                fallback_errors.append(f"{label}: {_short_error(exc)}")
        if last_error is not None:
            raise RuntimeError("All API mode candidates failed. " + "; ".join(fallback_errors)) from last_error
        raise RuntimeError("No API mode candidates were available.")

    def _create_responses_plan(self, user_prompt: str) -> str:
        response = self._client.responses.create(
            model=self._model,
            instructions=SYSTEM_PROMPT,
            input=user_prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "coding_agent_plan",
                    "schema": PLAN_SCHEMA,
                    "strict": True,
                }
            },
            max_output_tokens=self._max_output_tokens,
        )
        return _response_text(response)

    def _create_chat_plan(self, user_prompt: str, *, client: Any | None = None) -> str:
        client = client or self._client
        response = _chat_completion_create(
            client,
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_output_tokens=self._max_output_tokens,
        )
        return response.choices[0].message.content or ""

    def _create_azure_v1_responses_plan(self, user_prompt: str) -> str:
        client = self._azure_v1_client()
        response = client.responses.create(
            model=self._model,
            instructions=SYSTEM_PROMPT,
            input=user_prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "coding_agent_plan",
                    "schema": PLAN_SCHEMA,
                    "strict": True,
                }
            },
            max_output_tokens=self._max_output_tokens,
        )
        return _response_text(response)

    def _create_azure_v1_chat_plan(self, user_prompt: str) -> str:
        return self._create_chat_plan(user_prompt, client=self._azure_v1_client())

    def _create_foundry_models_chat_plan(self, user_prompt: str) -> str:
        return self._create_chat_plan(user_prompt, client=self._foundry_models_client())

    def _create_azure_deployment_chat_plan(self, user_prompt: str) -> str:
        if not self._base_url:
            raise RuntimeError("Azure fallback needs APIM_BASE_URL or OPENAI_BASE_URL.")
        endpoint = _azure_endpoint_from_base_url(self._base_url)
        client = self._AzureOpenAI(
            api_key=self._api_key,
            azure_endpoint=endpoint,
            api_version=self._azure_api_version,
            default_headers=_azure_headers(self._api_key, self._api_key_header),
            timeout=self._request_timeout,
        )
        return self._create_chat_plan(user_prompt, client=client)

    def _azure_v1_client(self) -> Any:
        if not self._base_url:
            raise RuntimeError("Azure v1 fallback needs APIM_BASE_URL or OPENAI_BASE_URL.")
        endpoint = _azure_endpoint_from_base_url(self._base_url)
        return self._OpenAI(
            api_key=self._api_key,
            base_url=endpoint.rstrip("/") + "/openai/v1",
            default_headers=_azure_headers(self._api_key, self._api_key_header),
            timeout=self._request_timeout,
        )

    def _foundry_models_client(self) -> Any:
        if not self._base_url:
            raise RuntimeError("Foundry Models fallback needs APIM_BASE_URL or OPENAI_BASE_URL.")
        return self._OpenAI(
            api_key=self._api_key,
            base_url=_append_path_once(self._base_url, "models"),
            default_headers=_azure_headers(self._api_key, self._api_key_header),
            timeout=self._request_timeout,
        )


def _response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "\n".join(chunks)

    return str(response)


def _chat_completion_create(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_output_tokens: int,
) -> Any:
    attempts = [
        (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "coding_agent_plan",
                    "schema": PLAN_SCHEMA,
                    "strict": True,
                },
            },
            "max_completion_tokens",
        ),
        (
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "coding_agent_plan",
                    "schema": PLAN_SCHEMA,
                    "strict": True,
                },
            },
            "max_tokens",
        ),
        ({"type": "json_object"}, "max_completion_tokens"),
        ({"type": "json_object"}, "max_tokens"),
    ]
    last_error: Exception | None = None
    for response_format, token_parameter in attempts:
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "response_format": response_format,
                token_parameter: max_output_tokens,
            }
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            last_error = exc
            if not _should_retry_chat_request(exc):
                raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("No chat completion attempts were available.")


def _azure_endpoint_from_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    for suffix in ("/openai/v1", "/openai", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _azure_headers(api_key: str, api_key_header: str | None) -> dict[str, str]:
    headers = {"api-key": api_key}
    if api_key_header:
        headers[api_key_header] = api_key
    else:
        headers["Ocp-Apim-Subscription-Key"] = api_key
    return headers


def _append_path_once(base_url: str, path_segment: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    suffix = "/" + path_segment.strip("/")
    if not path.endswith(suffix):
        path += suffix
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _short_error(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code:
        return f"HTTP {status_code}"
    return exc.__class__.__name__


def _should_try_next_endpoint(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {404, 405}:
        return True
    if status_code == 400:
        message = str(exc).lower()
        return any(marker in message for marker in ("responses", "not found", "unsupported", "unknown url"))
    return False


def _should_retry_chat_request(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code != 400:
        return False
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "json_schema",
            "response_format",
            "max_completion_tokens",
            "max_tokens",
            "unsupported",
            "unrecognized",
            "unknown parameter",
        )
    )
