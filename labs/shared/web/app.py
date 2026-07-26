from __future__ import annotations

import importlib
import traceback
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from labs.shared.config import ROOT_DIR, load_settings, save_google_api_key
from labs.shared.web.contracts import (
    ApiKeyRequest,
    ChatRequest,
    ChatResponse,
    ErrorInfo,
    EvalRequest,
    EvalResponse,
    HarnessEvent,
    MaterialKind,
    TextMaterialRequest,
    UrlMaterialRequest,
)
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.materials import MAX_FILE_BYTES, MaterialStore
from labs.shared.web.registry import StageRegistration, load_registry
from labs.shared.web.tracing import safe_artifact_path


STATIC_DIR = Path(__file__).with_name("static")


# App startup stays independent from student modules; stage code is imported per request.
def create_app(material_store: MaterialStore | None = None) -> FastAPI:
    application = FastAPI(title="Harness Lab", version="0.1.0")
    application.state.material_store = material_store or MaterialStore()
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        error = safe_error(exc)
        return JSONResponse(status_code=500, content={"detail": error.model_dump()})

    @application.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        settings = load_settings()
        return {
            "status": "ok",
            "model": settings.model,
            "model_mode": "live" if settings.google_api_key else "missing_api_key",
        }

    @application.post("/api/settings/api-key")
    def update_api_key(request: ApiKeyRequest) -> dict[str, str]:
        try:
            save_google_api_key(request.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "saved"}

    @application.get("/api/stages")
    def stages() -> dict[str, Any]:
        return {"stages": [registration.public.model_dump() for registration in load_registry().values()]}

    @application.get("/api/backends")
    def backends() -> dict[str, Any]:
        return {
            "backends": [
                {"id": "thin_harness", "title": "Thin harness", "available": True},
                {"id": "openclaw", "title": "OpenClaw", "available": openclaw_backend_available()},
            ]
        }

    @application.get("/api/materials")
    def list_materials(workspace_id: str) -> dict[str, Any]:
        return {"materials": [item.model_dump() for item in store(application).list(workspace_id)]}

    @application.post("/api/materials/defaults")
    def default_materials(workspace_id: str) -> dict[str, Any]:
        try:
            items = store(application).create_defaults(workspace_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"materials": [item.model_dump() for item in items]}

    @application.post("/api/materials/text")
    def add_text_material(request: TextMaterialRequest) -> dict[str, Any]:
        if request.kind == "web_source":
            raise HTTPException(status_code=400, detail="Add public job or company URLs from Lab 3 instead.")
        try:
            item = store(application).create_text(
                request.workspace_id,
                request.kind,
                request.display_name,
                request.text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return item.model_dump()

    @application.post("/api/materials/upload")
    async def upload_material(
        workspace_id: str = Form(),
        kind: MaterialKind = Form(),
        file: UploadFile = File(),
    ) -> dict[str, Any]:
        if kind == "web_source":
            raise HTTPException(status_code=400, detail="Add public job or company URLs from Lab 3 instead.")
        # Bound memory before parsing; reading an untrusted upload without a
        # size cap would make the later 5 MB validation too late.
        content = await file.read(MAX_FILE_BYTES + 1)
        try:
            item = store(application).create_upload(
                workspace_id,
                kind,
                file.filename or "upload",
                file.content_type,
                content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()
        return item.model_dump()

    @application.post("/api/materials/url")
    def add_web_source(request: UrlMaterialRequest) -> dict[str, Any]:
        try:
            item = store(application).create_url(request.workspace_id, request.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return item.model_dump()

    @application.delete("/api/materials/{material_id}")
    def delete_material(material_id: str, workspace_id: str) -> dict[str, str]:
        try:
            store(application).delete(workspace_id, material_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "deleted"}

    @application.delete("/api/materials")
    def clear_materials(workspace_id: str) -> dict[str, str]:
        try:
            store(application).clear(workspace_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "cleared"}

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        registration = load_registry().get(request.stage)
        if registration is None:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        if not registration.public.available:
            return unavailable_response(registration)
        try:
            adapter_class = load_adapter(registration.adapter)
            adapter = adapter_class(store(application))
            return adapter.chat(request)
        except StageExecutionError as exc:
            return error_response(request.stage, exc.run_id, exc.original, exc.events, exc.artifacts)
        except Exception as exc:
            run_id = f"run_{uuid.uuid4().hex}"
            event = HarnessEvent(
                sequence=1,
                type="error",
                status="failed",
                component="stage_adapter",
                operation="import_or_run",
                summary=f"Stage adapter failed with {type(exc).__name__}",
            )
            return error_response(request.stage, run_id, exc, [event], [])

    @application.post("/api/evals/run", response_model=EvalResponse)
    def run_eval(request: EvalRequest) -> EvalResponse:
        registration = load_registry().get(request.stage)
        if registration is None:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        if not registration.public.available:
            raise HTTPException(status_code=409, detail="This Lab stage is not available yet.")
        try:
            adapter_class = load_adapter(registration.adapter)
            adapter = adapter_class(store(application))
            return adapter.run_eval(request.workspace_id)
        except NotImplementedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StageExecutionError as exc:
            return EvalResponse(
                status="error",
                stage=request.stage,
                run_id=exc.run_id,
                events=exc.events,
                artifacts=exc.artifacts,
                error=safe_error(exc.original),
            )
        except Exception as exc:
            run_id = f"run_{uuid.uuid4().hex}"
            return EvalResponse(
                status="error",
                stage=request.stage,
                run_id=run_id,
                events=[
                    HarnessEvent(
                        sequence=1,
                        type="error",
                        status="failed",
                        component="stage_adapter",
                        operation="run_eval",
                        summary=f"Eval adapter failed with {type(exc).__name__}",
                    )
                ],
                error=safe_error(exc),
            )

    @application.get("/api/artifacts/{artifact_path:path}")
    def artifact(artifact_path: str) -> FileResponse:
        try:
            path = safe_artifact_path(artifact_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found.")
        if path.stat().st_size > 1_000_000:
            raise HTTPException(status_code=413, detail="Artifact is too large to display.")
        return FileResponse(path, media_type="text/plain; charset=utf-8")

    return application


def store(application: FastAPI) -> MaterialStore:
    return application.state.material_store


def load_adapter(path: str) -> type:
    module_name, class_name = path.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def openclaw_backend_available() -> bool:
    try:
        runtime = importlib.import_module("labs.lab_07.app.runtime")
        return bool(runtime.openclaw_available())
    except Exception:
        return False


def unavailable_response(registration: StageRegistration) -> ChatResponse:
    run_id = f"run_{uuid.uuid4().hex}"
    return ChatResponse(
        status="error",
        stage=registration.public.id,
        run_id=run_id,
        events=[
            HarnessEvent(
                sequence=1,
                type="error",
                status="blocked",
                component="stage_registry",
                operation="load_stage",
                summary=f"{registration.public.title} is not connected to the UI yet.",
            )
        ],
        error=ErrorInfo(
            kind="StageUnavailable",
            message="Finish the previous Lab and use its handout while this stage is being implemented.",
        ),
    )


def error_response(
    stage: str,
    run_id: str,
    exc: Exception,
    events: list[HarnessEvent],
    artifacts: list,
) -> ChatResponse:
    return ChatResponse(
        status="error",
        stage=stage,
        run_id=run_id,
        events=events,
        artifacts=artifacts,
        error=safe_error(exc),
    )


def safe_error(exc: Exception) -> ErrorInfo:
    settings = load_settings()
    message = str(exc) or type(exc).__name__
    if settings.google_api_key:
        message = message.replace(settings.google_api_key, "[REDACTED]")

    file_name: str | None = None
    line: int | None = None
    root = ROOT_DIR.resolve()
    if isinstance(exc, SyntaxError) and exc.filename:
        syntax_path = Path(exc.filename).resolve()
        if syntax_path == root or root in syntax_path.parents:
            file_name = syntax_path.relative_to(root).as_posix()
            line = exc.lineno
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if file_name is not None:
            break
        path = Path(frame.filename).resolve()
        if path == root or root in path.parents:
            file_name = path.relative_to(root).as_posix()
            line = frame.lineno
            break
    return ErrorInfo(kind=type(exc).__name__, message=message, file=file_name, line=line)


app = create_app()
