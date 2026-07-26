import os
from pathlib import Path

import httpx
import pytest

import labs.shared.config as config


def test_load_settings_reflects_env_file_edits_without_restarting(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_API_KEY=first-key\nLLM_MODEL=first-model\n", encoding="utf-8")

    first = config.load_settings()
    env_file.write_text("GOOGLE_API_KEY=second-key\nLLM_MODEL=second-model\n", encoding="utf-8")
    second = config.load_settings()

    assert first == config.Settings(model="first-model", google_api_key="first-key")
    assert second == config.Settings(model="second-model", google_api_key="second-key")


def test_process_environment_still_overrides_env_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    (tmp_path / ".env").write_text("GOOGLE_API_KEY=file-key\nLLM_MODEL=file-model\n", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_API_KEY", "shell-key")
    monkeypatch.setenv("LLM_MODEL", "shell-model")

    settings = config.load_settings()

    assert settings == config.Settings(model="shell-model", google_api_key="shell-key")


def test_save_google_api_key_updates_only_its_env_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Local settings\nLLM_MODEL=course-model\nGOOGLE_API_KEY=old-key\nGOOGLE_API_KEY=duplicate\n",
        encoding="utf-8",
    )

    config.save_google_api_key("  new-key  ")

    assert env_file.read_text(encoding="utf-8") == (
        "# Local settings\nLLM_MODEL=course-model\nGOOGLE_API_KEY=new-key\n"
    )
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_save_google_api_key_replaces_existing_process_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "stale-shell-key")

    config.save_google_api_key("new-dialog-key")

    assert os.environ["GOOGLE_API_KEY"] == "new-dialog-key"
    assert config.load_settings().google_api_key == "new-dialog-key"


@pytest.mark.parametrize("api_key", ["", "   ", "first\nsecond", "first\rsecond"])
def test_save_google_api_key_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch,
    api_key: str,
) -> None:
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)

    with pytest.raises(ValueError):
        config.save_google_api_key(api_key)

    assert not (tmp_path / ".env").exists()


def test_load_settings_removes_all_ipv6_cidrs_from_proxy_environment(monkeypatch) -> None:
    proxy_entries = "localhost,127.0.0.1,::1,::1/128,fd00::/8,fe80::/10,example.test"
    monkeypatch.setenv("NO_PROXY", proxy_entries)
    monkeypatch.setenv("no_proxy", proxy_entries)

    with pytest.raises(httpx.InvalidURL, match="Invalid port"):
        httpx.Client()

    config.load_settings()

    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1,::1,example.test"
    assert os.environ["no_proxy"] == "localhost,127.0.0.1,::1,example.test"
    with httpx.Client():
        pass


def test_load_settings_sanitizes_mixed_case_no_proxy_name(monkeypatch) -> None:
    for variable in tuple(os.environ):
        if variable.casefold() == "no_proxy":
            monkeypatch.delenv(variable)
    monkeypatch.setenv("No_Proxy", "::1/128")

    with pytest.raises(httpx.InvalidURL, match="Invalid port"):
        httpx.Client()

    config.load_settings()

    assert "No_Proxy" not in os.environ
    with httpx.Client():
        pass
