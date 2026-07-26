from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

import labs.shared.config as config
import labs.shared.web.app as web_app
from labs.lab_01.src.model_client import ModelClient
from labs.shared.config import ROOT_DIR, Settings
from labs.shared.web.app import create_app
from labs.shared.web.materials import MaterialStore


WORKSPACE = "workspace_webtest1"
SESSION = "session_webtest01"


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(MaterialStore(tmp_path / "materials")))


def require_stage(stage_id: str) -> None:
    if not (ROOT_DIR / "labs" / stage_id / "stage.json").is_file():
        pytest.skip(f"{stage_id} is not installed in this progressive workspace")


def test_health_and_static_shell_load_without_student_import(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    health = client.get("/api/health")
    page = client.get("/")
    logo = client.get("/static/assets/cai-logo.png")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert page.status_code == 200
    assert "Job materials" in page.text
    assert "Harness Inspector" in page.text
    assert "Run eval suite" in page.text
    assert "Reset to synthetic fixtures" not in page.text
    assert 'id="reset-materials"' not in page.text
    assert 'id="clear-materials"' in page.text
    assert 'aria-label="Clear materials"' in page.text
    assert "/static/app.js" in page.text
    assert '/static/assets/cai-logo.png' in page.text
    assert 'class="brand-mark"' not in page.text
    assert 'id="reload-agent"' in page.text
    assert 'aria-label="Reload Python and .env settings"' in page.text
    assert ">Reload Agent</button>" not in page.text
    assert 'id="clear-chat"' in page.text
    assert 'aria-label="Clear conversation"' in page.text
    assert "Reset conversation" not in page.text
    assert 'id="connection-status"' in page.text
    assert 'title="Configure Gemini API key"' in page.text
    assert 'id="api-key-dialog"' in page.text
    assert 'id="api-key-input" type="password"' in page.text
    assert 'class="trajectory-scroll"' in page.text
    assert 'id="materials-pagination"' in page.text
    assert logo.status_code == 200
    assert logo.headers["content-type"] == "image/png"


def test_api_key_endpoint_saves_locally_without_echoing_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    client = make_client(tmp_path)
    secret = "test-gemini-secret"

    response = client.post("/api/settings/api-key", json={"api_key": secret})

    assert response.status_code == 200
    assert response.json() == {"status": "saved"}
    assert secret not in response.text
    assert (tmp_path / ".env").read_text(encoding="utf-8") == f"GOOGLE_API_KEY={secret}\n"
    assert client.get("/api/health").json()["model_mode"] == "live"


def test_api_key_endpoint_rejects_multiline_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)

    response = make_client(tmp_path).post(
        "/api/settings/api-key",
        json={"api_key": "first-line\nsecond-line"},
    )

    assert response.status_code == 400
    assert not (tmp_path / ".env").exists()


def test_api_key_endpoint_does_not_echo_rejected_secret(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "ROOT_DIR", tmp_path)
    secret = "s" * 513

    response = make_client(tmp_path).post("/api/settings/api-key", json={"api_key": secret})

    assert response.status_code == 400
    assert secret not in response.text
    assert not (tmp_path / ".env").exists()


def test_stage_registry_exposes_capability_and_limitation(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    stages = client.get("/api/stages").json()["stages"]
    installed_stage_ids = [
        path.parent.name
        for path in sorted((ROOT_DIR / "labs").glob("lab_*/stage.json"))
    ]
    stages_by_id = {stage["id"]: stage for stage in stages}

    assert [stage["id"] for stage in stages] == installed_stage_ids
    assert stages[0]["available"] is True
    assert stages[0]["now_you_can"]
    assert stages[0]["examples"]
    assert "cannot" in stages[0]["still_cannot"].lower()
    if "lab_02" in stages_by_id:
        assert "arbitrary follow-up" in stages_by_id["lab_02"]["still_cannot"]
    if "lab_03" in stages_by_id:
        assert "unreachable queued URL" in stages_by_id["lab_03"]["still_cannot"]

    backends = client.get("/api/backends").json()["backends"]
    assert backends[0] == {"id": "thin_harness", "title": "Thin harness", "available": True}
    assert backends[1]["id"] == "openclaw"


def test_backends_degrades_when_openclaw_runtime_is_broken(tmp_path: Path, monkeypatch) -> None:
    real_import = web_app.importlib.import_module

    def broken_runtime_import(name: str):
        if name == "labs.lab_07.app.runtime":
            raise SyntaxError("student changed protected runtime")
        return real_import(name)

    monkeypatch.setattr(web_app.importlib, "import_module", broken_runtime_import)

    backends = make_client(tmp_path).get("/api/backends")

    assert backends.status_code == 200
    assert backends.json()["backends"][1] == {"id": "openclaw", "title": "OpenClaw", "available": False}


def test_safe_error_redacts_api_key_and_reports_syntax_location(monkeypatch) -> None:
    secret = "super-secret-api-key"
    monkeypatch.setattr(web_app, "load_settings", lambda: Settings(model="fake", google_api_key=secret))

    redacted = web_app.safe_error(ValueError(f"provider rejected {secret}"))
    syntax_path = ROOT_DIR / "labs" / "lab_01" / "src" / "student_syntax.py"
    syntax = web_app.safe_error(SyntaxError("invalid syntax", (str(syntax_path), 23, 5, "broken")))

    assert secret not in redacted.message
    assert "[REDACTED]" in redacted.message
    assert syntax.file == "labs/lab_01/src/student_syntax.py"
    assert syntax.line == 23


def test_frontend_bounds_chat_requests_and_recovers_application_data() -> None:
    source = (ROOT_DIR / "labs" / "shared" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert ".slice(-MAX_REQUEST_MESSAGES)" in source
    assert ".slice(0, MAX_REQUEST_MESSAGE_CHARACTERS)" in source
    assert "readableErrorDetail(data?.detail" in source
    assert "if (shouldReloadApplicationData) await loadApplicationData();" in source
    assert "async function reloadAgent()" in source
    assert 'showToast("Agent reloaded. Python and .env settings are current.")' in source
    assert "DEBUG_LOG_LIMIT = 100" in source
    assert 'console[method]("[Harness Lab]", entry)' in source
    assert "navigator.clipboard.writeText" in source
    assert "workspace_id=[redacted]" in source
    assert 'window.addEventListener("error"' in source
    assert 'window.addEventListener("unhandledrejection"' in source
    assert source.count("restoreDraft(content);") == 2
    assert 'elements.reloadAgent.classList.add("is-reloading")' in source
    assert 'elements.reloadAgent.classList.remove("is-reloading")' in source
    assert 'body: JSON.stringify({ api_key: apiKey })' in source
    assert 'elements.apiKeyInput.value = "";' in source
    assert "function renderMarkdown(markdown)" in source
    assert "function renderInlineMarkdown(value)" in source
    assert "function safeMarkdownHref(value)" in source
    assert "(?:\\s+#+)?" in source
    assert "<strong>" in source
    assert "<pre><code" in source
    assert "target=\"_blank\" rel=\"noopener noreferrer\"" in source
    assert "escapeHtml(message.content).replace(/\\n/g, \"<br />\")" in source


def test_desktop_layout_limits_scrolling_to_chat_and_trajectory() -> None:
    source = (ROOT_DIR / "labs" / "shared" / "web" / "static" / "styles.css").read_text(encoding="utf-8")

    assert "body {\n  margin: 0;\n  height: 100dvh;" in source
    assert ".app-shell {\n  min-height: 0;\n  flex: 1 1 auto;\n  overflow: hidden;" in source
    assert ".chat-transcript { min-height: 0; padding: 18px; overflow-y: auto;" in source
    assert ".trajectory-scroll { min-height: 0; overflow-y: auto;" in source
    assert ".materials-list { min-height: 0; display: grid; gap: 9px; overflow: hidden; }" in source
    assert ".materials-pagination" in source
    assert "@media (max-width: 820px)" in source


def test_material_sources_use_pagination_instead_of_a_third_scroll_region() -> None:
    source = (ROOT_DIR / "labs" / "shared" / "web" / "static" / "app.js").read_text(encoding="utf-8")

    assert 'const WEB_SOURCES_PER_PAGE = window.matchMedia("(max-height: 800px)").matches ? 1 : 2;' in source
    assert "renderMaterialsPagination(webSources.length" in source
    assert "Sources ${pageStart + 1}–${pageStart + visibleCount} of ${sourceCount}" in source
    assert 'localStorage.getItem("harness.defaultsStage")' in source
    assert "hasCoreMaterials" in source
    assert "needsLab3Defaults" in source
    assert 'id="restore-example-materials"' in source
    assert "restoreExampleMaterials" in source
    assert "resetJobWorkspace" not in source


def test_unhandled_server_error_returns_safe_debug_location(tmp_path: Path, monkeypatch) -> None:
    client = TestClient(
        create_app(MaterialStore(tmp_path / "materials")),
        raise_server_exceptions=False,
    )

    def broken_defaults(_: str):
        raise FileNotFoundError("missing synthetic fixture")

    monkeypatch.setattr(client.app.state.material_store, "create_defaults", broken_defaults)

    response = client.post("/api/materials/defaults", params={"workspace_id": WORKSPACE})

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "kind": "FileNotFoundError",
        "message": "missing synthetic fixture",
        "file": "labs/shared/tests/test_web_app.py",
        "line": response.json()["detail"]["line"],
    }
    assert isinstance(response.json()["detail"]["line"], int)


def test_material_text_list_and_delete_contract(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = client.post(
        "/api/materials/text",
        json={
            "workspace_id": WORKSPACE,
            "kind": "candidate_profile",
            "display_name": "Synthetic resume",
            "text": "Python backend engineer",
        },
    )

    assert response.status_code == 200
    material_id = response.json()["material_id"]
    listed = client.get("/api/materials", params={"workspace_id": WORKSPACE}).json()["materials"]
    assert listed[0]["material_id"] == material_id
    assert "text" not in listed[0]

    deleted = client.delete(f"/api/materials/{material_id}", params={"workspace_id": WORKSPACE})
    assert deleted.status_code == 200
    assert client.get("/api/materials", params={"workspace_id": WORKSPACE}).json()["materials"] == []


def test_web_source_url_endpoint_queues_page_for_lab_3_instead_of_fetching_in_ui(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    queued = client.post(
        "/api/materials/url",
        json={"workspace_id": WORKSPACE, "url": "https://example.com/company/engineering"},
    )
    manual_source = client.post(
        "/api/materials/text",
        json={
            "workspace_id": WORKSPACE,
            "kind": "web_source",
            "display_name": "Vague source",
            "text": "Students should not upload this manually.",
        },
    )

    assert queued.status_code == 200
    assert queued.json()["status"] == "pending"
    assert queued.json()["source"] == "web"
    assert queued.json()["source_url"] == "https://example.com/company/engineering"
    assert manual_source.status_code == 400
    assert "URLs from Lab 3" in manual_source.json()["detail"]


def test_job_material_docx_upload_is_parsed_locally_and_exposes_no_raw_file(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    document = Document()
    document.add_paragraph("Python backend engineer with workflow automation experience.")
    payload = io.BytesIO()
    document.save(payload)

    response = client.post(
        "/api/materials/upload",
        data={"workspace_id": WORKSPACE, "kind": "candidate_profile"},
        files={
            "file": (
                "resume.docx",
                payload.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    item = response.json()
    assert item["display_name"] == "resume.docx"
    assert item["source"] == "upload"
    assert "text" not in item
    stored = list((tmp_path / "materials" / WORKSPACE).iterdir())
    assert stored == [tmp_path / "materials" / WORKSPACE / f"{item['material_id']}.json"]
    assert not list((tmp_path / "materials").rglob("*.docx"))


def test_job_material_upload_rejects_unsupported_and_oversized_files(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    unsupported = client.post(
        "/api/materials/upload",
        data={"workspace_id": WORKSPACE, "kind": "candidate_profile"},
        files={"file": ("resume.py", b"print('not a resume')", "text/x-python")},
    )
    oversized = client.post(
        "/api/materials/upload",
        data={"workspace_id": WORKSPACE, "kind": "candidate_profile"},
        files={"file": ("resume.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")},
    )

    assert unsupported.status_code == 400
    assert "Supported formats" in unsupported.json()["detail"]
    assert oversized.status_code == 400
    assert "5 MB" in oversized.json()["detail"]


def test_lab_1_chat_calls_student_boundary_and_returns_explicit_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = make_client(tmp_path)
    client.post(
        "/api/materials/text",
        json={
            "workspace_id": WORKSPACE,
            "kind": "candidate_profile",
            "display_name": "Synthetic resume",
            "text": "Python backend engineer",
        },
    )
    client.app.state.material_store.create_text(
        WORKSPACE,
        "web_source",
        "Hidden Lab 3 source",
        "SECRET_WEB_SOURCE_SHOULD_NOT_APPEAR_IN_LAB_1",
        source="fixture",
    )
    captured: dict = {}

    def fake_complete(self: ModelClient, messages: list[dict[str, str]]) -> str:
        captured["messages"] = messages
        self.last_metadata = {"model": "fake-gemini", "estimated_tokens": 42, "latency_ms": 1}
        return "A useful but unstructured fit and gap answer."

    monkeypatch.setattr(ModelClient, "complete", fake_complete)
    response = client.post(
        "/api/chat",
        json={
            "stage": "lab_01",
            "session_id": SESSION,
            "workspace_id": WORKSPACE,
            "messages": [{"role": "user", "content": "Explain my fit and gaps."}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["assistant_message"].startswith("A useful")
    assert "Python backend engineer" in captured["messages"][0]["content"]
    assert "SECRET_WEB_SOURCE_SHOULD_NOT_APPEAR_IN_LAB_1" not in captured["messages"][0]["content"]
    assert payload["events"] == [
        {
            "sequence": 1,
            "type": "model_call",
            "status": "completed",
            "component": "labs.lab_01.src.model_client.ModelClient",
            "operation": "complete",
            "summary": "Called the student's raw Gemini model boundary",
            "duration_ms": payload["events"][0]["duration_ms"],
            "details": payload["events"][0]["details"],
        }
    ]
    assert payload["events"][0]["details"]["material_ids"]
    assert payload["state_summary"]["structured_state"] is False
    assert payload["artifacts"][0]["path"].endswith("trace.jsonl")

    artifact = client.get(f"/api/artifacts/{payload['artifacts'][0]['path']}")
    assert artifact.status_code == 200
    assert "ModelClient" in artifact.text
    assert payload["run_id"] in artifact.text


def test_unavailable_openclaw_backend_is_honest_instead_of_faking_success(tmp_path: Path) -> None:
    require_stage("lab_07")
    client = make_client(tmp_path)

    response = client.post(
        "/api/chat",
        json={
            "stage": "lab_07",
            "backend": "openclaw",
            "session_id": SESSION,
            "workspace_id": WORKSPACE,
            "messages": [{"role": "user", "content": "Run the OpenClaw product."}],
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["error"]["kind"] == "RuntimeError"
    assert "OpenClaw" in response.json()["error"]["message"]
    assert response.json()["events"][0]["status"] == "failed"


def test_eval_endpoint_reports_missing_cumulative_run_as_traceable_error(tmp_path: Path) -> None:
    require_stage("lab_05")
    client = make_client(tmp_path)

    response = client.post(
        "/api/evals/run",
        json={"stage": "lab_05", "workspace_id": WORKSPACE},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["events"][0]["component"] == "labs.lab_05.src.evals"
    assert "Lab 4" in response.json()["error"]["message"]


def test_artifact_route_rejects_paths_outside_artifacts(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/artifacts/%2E%2E/pyproject.toml")

    assert response.status_code in {400, 404}
