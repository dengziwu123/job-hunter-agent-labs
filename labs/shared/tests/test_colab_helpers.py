from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from labs.shared.colab import list_artifacts, require_google_api_key, run_lab_tests, setup_colab_workspace


def test_setup_colab_workspace_adds_root_and_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    returned = setup_colab_workspace(root)

    assert returned == root.resolve()
    assert str(root.resolve()) in sys.path
    assert (root / "artifacts").is_dir()


def test_require_google_api_key_uses_existing_environment(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

    assert require_google_api_key() == "test-key"


def test_require_google_api_key_prompts_and_stores_value(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("labs.shared.colab.getpass", lambda prompt: "typed-key")

    assert require_google_api_key() == "typed-key"
    assert os.environ["GOOGLE_API_KEY"] == "typed-key"


def test_require_google_api_key_rejects_blank_prompt(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("labs.shared.colab.getpass", lambda prompt: " ")

    with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
        require_google_api_key()


def test_list_artifacts_returns_relative_files(tmp_path: Path) -> None:
    artifact = tmp_path / "artifacts" / "lab_01" / "baseline.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")

    assert list_artifacts("lab_01", root=tmp_path) == ["artifacts/lab_01/baseline.json"]


def test_run_lab_tests_uses_lab_specific_pytest_path(tmp_path: Path) -> None:
    test_path = tmp_path / "labs" / "lab_fake" / "tests" / "test_smoke.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text("def test_smoke():\n    assert True\n", encoding="utf-8")

    result = run_lab_tests("lab_fake", root=tmp_path)

    assert result.returncode == 0
    assert "1 passed" in result.stdout
