from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from labs.shared.config import ROOT_DIR, load_settings


def main() -> None:
    settings = load_settings()
    tests_dir = ROOT_DIR / "labs" / "lab_01" / "tests"

    print(f"OK python_version={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print("OK dependency_check=true")
    print(f"OK lab_tests_discovered={tests_dir.exists()}")
    print(f"OK google_api_key_present={bool(settings.google_api_key)}")
    try:
        google_genai_installed = importlib.util.find_spec("google.genai") is not None
    except ModuleNotFoundError:
        google_genai_installed = False
    print(f"OK google_genai_installed={google_genai_installed}")

    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11+ is required.")

    if not tests_dir.exists():
        raise SystemExit(f"Missing tests directory: {tests_dir}")

    if not settings.google_api_key:
        raise SystemExit("GOOGLE_API_KEY is required for Lab 1 Gemini API baseline.")

    if not google_genai_installed:
        raise SystemExit("google-genai is required. Run `uv sync`.")


if __name__ == "__main__":
    main()
