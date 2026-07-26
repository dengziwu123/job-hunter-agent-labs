from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from ipaddress import IPv6Network, ip_network
from pathlib import Path
from typing import Iterator, Literal


ROOT_DIR = Path(__file__).resolve().parents[2]
Provider = Literal["gemini", "openai", "anthropic"]
DEFAULT_MODELS: dict[Provider, str] = {
    "gemini": "gemini-flash-latest",
    "openai": "gpt-5-mini",
    "anthropic": "claude-haiku-4-5",
}
API_KEY_ENV_VARS: dict[Provider, str] = {
    "gemini": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    model: str
    google_api_key: str = ""
    provider: Provider = "gemini"
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    @property
    def api_key(self) -> str:
        return {
            "gemini": self.google_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
        }[self.provider]

    @property
    def api_key_env_var(self) -> str:
        return API_KEY_ENV_VARS[self.provider]


_SETTINGS_OVERRIDE: ContextVar[Settings | None] = ContextVar(
    "harness_settings_override",
    default=None,
)


@contextmanager
def frozen_model_settings(settings: Settings) -> Iterator[None]:
    """Keep one model configuration stable across an isolated comparison."""
    token = _SETTINGS_OVERRIDE.set(settings)
    try:
        yield
    finally:
        _SETTINGS_OVERRIDE.reset(token)


def sanitize_proxy_environment() -> None:
    """Remove proxy exclusions that httpx 0.28 cannot parse.

    Some teaching environments inject IPv6 CIDRs such as ``::1/128`` into a
    NO_PROXY variant. httpx 0.28 treats them as malformed URLs before a Gemini
    request is sent. Plain IPv6 hosts such as ``::1`` remain supported.
    """
    for variable in tuple(os.environ):
        if variable.casefold() != "no_proxy":
            continue
        value = os.environ.get(variable)
        if value is None:
            continue
        entries = [entry.strip() for entry in value.split(",")]
        cleaned = [entry for entry in entries if not _is_ipv6_cidr(entry)]
        if len(cleaned) == len(entries):
            continue
        if cleaned:
            os.environ[variable] = ",".join(cleaned)
        else:
            os.environ.pop(variable, None)


def _is_ipv6_cidr(value: str) -> bool:
    if "/" not in value:
        return False
    try:
        return isinstance(ip_network(value, strict=False), IPv6Network)
    except ValueError:
        return False


def load_settings() -> Settings:
    override = _SETTINGS_OVERRIDE.get()
    if override is not None:
        return override
    sanitize_proxy_environment()
    dotenv = _load_dotenv(ROOT_DIR / ".env") or {}
    provider = _load_provider(os.getenv("LLM_PROVIDER", dotenv.get("LLM_PROVIDER", "gemini")))
    configured_model = os.getenv("LLM_MODEL", dotenv.get("LLM_MODEL", "")).strip()
    api_keys = _api_keys(dotenv)

    return Settings(
        model=configured_model or DEFAULT_MODELS[provider],
        google_api_key=api_keys["gemini"],
        provider=provider,
        openai_api_key=api_keys["openai"],
        anthropic_api_key=api_keys["anthropic"],
    )


def load_api_keys() -> dict[Provider, str]:
    """Load provider secrets without validating LLM_PROVIDER."""
    return _api_keys(_load_dotenv(ROOT_DIR / ".env") or {})


def _api_keys(dotenv: dict[str, str]) -> dict[Provider, str]:
    return {
        provider: os.getenv(env_var, dotenv.get(env_var, "")).strip()
        for provider, env_var in API_KEY_ENV_VARS.items()
    }


def _load_provider(value: str) -> Provider:
    provider = value.strip().lower()
    if provider not in DEFAULT_MODELS:
        supported = ", ".join(DEFAULT_MODELS)
        raise ValueError(f"Unsupported LLM_PROVIDER {value!r}. Choose one of: {supported}.")
    return provider  # type: ignore[return-value]


def save_google_api_key(api_key: str) -> None:
    """Save a Gemini API key to this local course workspace."""
    save_provider_api_key("gemini", api_key)


def save_provider_api_key(provider: Provider, api_key: str) -> None:
    """Save one provider API key to this local course workspace."""
    value = _validate_setting(api_key, f"Enter a {provider.title()} API key.", 512)
    env_var = API_KEY_ENV_VARS[provider]
    _save_env_values({env_var: value})
    if env_var in os.environ:
        os.environ[env_var] = value


def save_provider_configuration(provider: Provider, api_key: str, model: str = "") -> None:
    """Select a provider/model and save its key from the local setup UI."""
    value = _validate_setting(api_key, f"Enter a {provider.title()} API key.", 512)
    selected_model = _validate_setting(
        model or DEFAULT_MODELS[provider],
        "Enter a model name.",
        200,
    )
    env_var = API_KEY_ENV_VARS[provider]
    _save_env_values(
        {
            "LLM_PROVIDER": provider,
            "LLM_MODEL": selected_model,
            env_var: value,
        }
    )
    os.environ["LLM_PROVIDER"] = provider
    os.environ["LLM_MODEL"] = selected_model
    os.environ[env_var] = value


def _validate_setting(value: str, empty_message: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(empty_message)
    if "\n" in cleaned or "\r" in cleaned:
        raise ValueError("Settings must be a single line.")
    if len(cleaned) > max_length:
        raise ValueError("The setting is too long.")
    return cleaned


def _save_env_values(values: dict[str, str]) -> None:
    env_path = ROOT_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated: list[str] = []
    remaining = dict(values)
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in values:
            if key in remaining:
                updated.append(f"{key}={remaining.pop(key)}")
            continue
        updated.append(line)
    updated.extend(f"{key}={value}" for key, value in remaining.items())

    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    env_path.chmod(0o600)
