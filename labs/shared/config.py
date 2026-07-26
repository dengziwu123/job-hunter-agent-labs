from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import IPv6Network, ip_network
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


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
    google_api_key: str


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
    sanitize_proxy_environment()
    dotenv = _load_dotenv(ROOT_DIR / ".env") or {}

    return Settings(
        model=os.getenv("LLM_MODEL", dotenv.get("LLM_MODEL", "gemini-flash-latest")).strip(),
        google_api_key=os.getenv("GOOGLE_API_KEY", dotenv.get("GOOGLE_API_KEY", "")).strip(),
    )


def save_google_api_key(api_key: str) -> None:
    """Save a Gemini API key to this local course workspace."""
    value = api_key.strip()
    if not value:
        raise ValueError("Enter a Gemini API key.")
    if "\n" in value or "\r" in value:
        raise ValueError("The API key must be a single line.")
    if len(value) > 512:
        raise ValueError("The API key is too long.")

    env_path = ROOT_DIR / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.lstrip().startswith("GOOGLE_API_KEY="):
            if not replaced:
                updated.append(f"GOOGLE_API_KEY={value}")
                replaced = True
            continue
        updated.append(line)
    if not replaced:
        updated.append(f"GOOGLE_API_KEY={value}")

    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    env_path.chmod(0o600)
    if "GOOGLE_API_KEY" in os.environ:
        os.environ["GOOGLE_API_KEY"] = value
