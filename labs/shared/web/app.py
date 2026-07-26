from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import traceback
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from labs.shared.config import (
    ROOT_DIR,
    load_api_keys,
    load_settings,
    save_google_api_key,
    save_provider_configuration,
)
from labs.shared.web.contracts import (
    ApiKeyRequest,
    ChatRequest,
    ChatResponse,
    ComparisonRequest,
    ComparisonResponse,
    ErrorInfo,
    EvalRequest,
    EvalResponse,
    HarnessEvent,
    MaterialKind,
    TextMaterialRequest,
    UrlMaterialRequest,
)
from labs.shared.web.errors import StageExecutionError
from labs.shared.web.execution_locks import workspace_execution_lock
from labs.shared.web.comparisons import (
    acknowledge_comparison_request,
    clear_comparison_artifacts,
    clear_comparison_request_cache,
    comparison_side_workspace_id,
    comparison_workspace_prefix,
    run_comparison,
)
from labs.shared.web.materials import MAX_FILE_BYTES, MaterialStore
from labs.shared.web.registry import StageRegistration, load_registry
from labs.shared.web.stage_executor import StageExecutor
from labs.shared.web.tracing import safe_artifact_path


STATIC_DIR = Path(__file__).with_name("static")
MAX_CHAT_REQUEST_CACHE_ENTRIES = 20


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
            "provider": settings.provider,
            "model": settings.model,
            "model_mode": "live" if settings.api_key else "missing_api_key",
        }

    @application.post("/api/settings/api-key")
    def update_api_key(request: ApiKeyRequest) -> dict[str, str]:
        try:
            if request.provider is None:
                save_google_api_key(request.api_key)
            else:
                save_provider_configuration(request.provider, request.api_key, request.model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "saved"}

    @application.get("/api/stages")
    def stages() -> dict[str, Any]:
        return {"stages": [registration.public.model_dump() for registration in load_registry().values()]}

    @application.get("/api/stages/{stage_id}/instructions")
    def stage_instructions(stage_id: str) -> dict[str, str]:
        registration = load_registry().get(stage_id)
        if registration is None:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        if not registration.public.available:
            raise HTTPException(status_code=409, detail="This Lab stage is not available yet.")
        instruction = find_instruction(stage_id)
        if instruction is None:
            raise HTTPException(status_code=404, detail="Instructions are not installed for this Lab stage.")
        path, source = instruction
        return {
            "stage": stage_id,
            "source": f"{source}/{path.name}",
            "markdown": path.read_text(encoding="utf-8"),
        }

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
    def clear_materials(
        workspace_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
    ) -> dict[str, str]:
        try:
            with workspace_execution_lock(workspace_id):
                store(application).clear(workspace_id)
                clear_managed_task_state(workspace_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "cleared"}

    @application.delete("/api/task-state")
    def clear_task_state(
        workspace_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        stage_id: str,
        session_id: Annotated[
            str | None,
            Query(pattern=r"^[A-Za-z0-9_-]{8,100}$"),
        ] = None,
    ) -> dict[str, str]:
        registry = load_registry()
        if stage_id not in registry:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        with workspace_execution_lock(workspace_id):
            clear_managed_task_state(workspace_id, stage_id, session_id=session_id)
        return {"status": "cleared"}

    @application.delete("/api/comparison-state")
    def clear_comparison_state(
        workspace_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        session_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        current_stage: str,
    ) -> dict[str, str]:
        registry = load_registry()
        registration = registry.get(current_stage)
        if registration is None:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        if not registration.public.previous_stage:
            raise HTTPException(status_code=409, detail="Lab 1 has no comparison state.")
        with workspace_execution_lock(workspace_id):
            clear_comparison_side_states(
                workspace_id,
                session_id,
                current_stage,
                registration.public.previous_stage,
            )
        return {"status": "cleared"}

    @application.delete("/api/chat-request")
    def acknowledge_chat(
        workspace_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        session_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        stage_id: str,
        request_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
    ) -> dict[str, str]:
        if stage_id not in load_registry():
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        acknowledge_chat_request(workspace_id, session_id, stage_id, request_id)
        return {"status": "acknowledged"}

    @application.delete("/api/comparison-request")
    def acknowledge_comparison(
        workspace_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        session_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        current_stage: str,
        request_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
    ) -> dict[str, str]:
        if current_stage not in load_registry():
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        acknowledge_comparison_request(
            workspace_id,
            session_id,
            current_stage,
            request_id,
        )
        return {"status": "acknowledged"}

    @application.post("/api/comparison-state/rollback")
    def rollback_comparison_state(
        workspace_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        session_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,100}$")],
        current_stage: str,
        comparison_id: Annotated[
            str,
            Query(pattern=r"^comparison_[a-f0-9]{32}$"),
        ],
    ) -> dict[str, str]:
        registry = load_registry()
        registration = registry.get(current_stage)
        if registration is None:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        previous_stage = registration.public.previous_stage
        if not previous_stage:
            raise HTTPException(status_code=409, detail="Lab 1 has no comparison state.")
        manifest_path = (
            ROOT_DIR
            / "artifacts"
            / "comparisons"
            / comparison_id
            / "comparison.json"
        )
        if not manifest_path.is_file():
            raise HTTPException(status_code=404, detail="Comparison result not found.")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="Comparison result is unreadable.") from exc
        if (
            manifest.get("before", {}).get("stage") != previous_stage
            or manifest.get("after", {}).get("stage") != current_stage
        ):
            raise HTTPException(status_code=409, detail="Comparison result does not match the selected Lab.")
        before_workspace = comparison_side_workspace_id(
            workspace_id,
            session_id,
            current_stage,
            "before",
        )
        after_workspace = comparison_side_workspace_id(
            workspace_id,
            session_id,
            current_stage,
            "after",
        )
        with workspace_execution_lock(workspace_id):
            restore_state_before_comparison_turn(
                previous_stage,
                before_workspace,
                manifest.get("before", {}),
            )
            restore_state_before_comparison_turn(
                current_stage,
                after_workspace,
                manifest.get("after", {}),
            )
        return {"status": "restored"}

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(request: ChatRequest) -> ChatResponse:
        registration = load_registry().get(request.stage)
        if registration is None:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        if not registration.public.available:
            return unavailable_response(registration)
        with workspace_execution_lock(request.workspace_id):
            try:
                cached_response = cached_chat_response(request)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if cached_response is not None:
                return cached_response
            try:
                response = StageExecutor(load_adapter).chat(
                    registration=registration,
                    materials=store(application),
                    request=request,
                )
            except StageExecutionError as exc:
                response = error_response(
                    request.stage,
                    exc.run_id,
                    exc.original,
                    exc.events,
                    exc.artifacts,
                )
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
                response = error_response(request.stage, run_id, exc, [event], [])
            cache_chat_response(request, response)
            return response

    @application.post("/api/comparisons", response_model=ComparisonResponse)
    def comparison(request: ComparisonRequest) -> ComparisonResponse:
        registration = load_registry().get(request.current_stage)
        if registration is None:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        if not registration.public.available:
            raise HTTPException(status_code=409, detail="This Lab stage is not available yet.")
        if not registration.public.previous_stage:
            raise HTTPException(status_code=409, detail="Lab 1 has no previous stage to compare.")
        try:
            return run_comparison(
                registry=load_registry(),
                load_adapter=load_adapter,
                materials=store(application),
                request=request,
                error_builder=safe_error,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @application.post("/api/evals/run", response_model=EvalResponse)
    def run_eval(request: EvalRequest) -> EvalResponse:
        registration = load_registry().get(request.stage)
        if registration is None:
            raise HTTPException(status_code=404, detail="Unknown Lab stage.")
        if not registration.public.available:
            raise HTTPException(status_code=409, detail="This Lab stage is not available yet.")
        try:
            return StageExecutor(load_adapter).eval(
                registration=registration,
                materials=store(application),
                workspace_id=request.workspace_id,
            )
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


def clear_managed_task_state(
    workspace_id: str,
    stage_id: str | None = None,
    *,
    session_id: str | None = None,
) -> None:
    if stage_id is None:
        clear_chat_request_cache(workspace_id)
        clear_comparison_request_cache(workspace_id)
        clear_comparison_artifacts(workspace_id)
    elif session_id is not None:
        clear_chat_request_cache(workspace_id, session_id, stage_id)
    stage_ids = [stage_id] if stage_id else list(load_registry())
    for current_stage_id in stage_ids:
        shutil.rmtree(
            managed_task_state_directory(current_stage_id, workspace_id),
            ignore_errors=True,
        )
        if stage_id is None:
            prefix = comparison_workspace_prefix(workspace_id)
            state_root = ROOT_DIR / "artifacts" / "task-state" / current_stage_id
            for path in state_root.glob(f"{prefix}*"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
    if stage_id and session_id:
        registration = load_registry()[stage_id]
        if registration.public.previous_stage:
            clear_comparison_side_states(
                workspace_id,
                session_id,
                stage_id,
                registration.public.previous_stage,
            )


def clear_comparison_side_states(
    workspace_id: str,
    session_id: str,
    current_stage: str,
    previous_stage: str,
) -> None:
    clear_comparison_request_cache(workspace_id, session_id, current_stage)
    clear_comparison_artifacts(workspace_id, session_id, current_stage)
    before_workspace = comparison_side_workspace_id(
        workspace_id,
        session_id,
        current_stage,
        "before",
    )
    after_workspace = comparison_side_workspace_id(
        workspace_id,
        session_id,
        current_stage,
        "after",
    )
    shutil.rmtree(
        managed_task_state_directory(previous_stage, before_workspace),
        ignore_errors=True,
    )
    shutil.rmtree(
        managed_task_state_directory(current_stage, after_workspace),
        ignore_errors=True,
    )


def chat_request_fingerprint(request: ChatRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"request_id"})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def chat_request_cache_directory(
    workspace_id: str,
    session_id: str | None = None,
    stage_id: str | None = None,
) -> Path:
    path = ROOT_DIR / "artifacts" / "chat-requests" / workspace_id
    if session_id is not None:
        path /= session_id
    if stage_id is not None:
        path /= stage_id
    return path


def chat_request_cache_file(
    workspace_id: str,
    session_id: str,
    stage_id: str,
    request_id: str,
) -> Path:
    return chat_request_cache_directory(workspace_id, session_id, stage_id) / f"{request_id}.json"


def cached_chat_response(request: ChatRequest) -> ChatResponse | None:
    if request.request_id is None:
        return None
    path = chat_request_cache_file(
        request.workspace_id,
        request.session_id,
        request.stage,
        request.request_id,
    )
    if not path.is_file():
        return None
    cached = json.loads(path.read_text(encoding="utf-8"))
    if cached.get("request_fingerprint") != chat_request_fingerprint(request):
        raise ValueError("Chat request id was already used for different input.")
    return ChatResponse.model_validate(cached["response"])


def cache_chat_response(request: ChatRequest, response: ChatResponse) -> None:
    if request.request_id is None:
        return
    path = chat_request_cache_file(
        request.workspace_id,
        request.session_id,
        request.stage,
        request.request_id,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "request_fingerprint": chat_request_fingerprint(request),
                "response": response.model_dump(mode="json"),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    cache_files = sorted(
        path.parent.glob("*.json"),
        key=lambda cached_path: cached_path.stat().st_mtime_ns,
        reverse=True,
    )
    for stale_path in cache_files[MAX_CHAT_REQUEST_CACHE_ENTRIES:]:
        stale_path.unlink(missing_ok=True)


def acknowledge_chat_request(
    workspace_id: str,
    session_id: str,
    stage_id: str,
    request_id: str,
) -> None:
    chat_request_cache_file(
        workspace_id,
        session_id,
        stage_id,
        request_id,
    ).unlink(missing_ok=True)


def clear_chat_request_cache(
    workspace_id: str,
    session_id: str | None = None,
    stage_id: str | None = None,
) -> None:
    shutil.rmtree(
        chat_request_cache_directory(workspace_id, session_id, stage_id),
        ignore_errors=True,
    )


def managed_task_state_file(stage_id: str, workspace_id: str) -> Path:
    return managed_task_state_directory(stage_id, workspace_id) / "task_state.json"


def managed_task_state_directory(stage_id: str, workspace_id: str) -> Path:
    return ROOT_DIR / "artifacts" / "task-state" / stage_id / workspace_id


def restore_state_before_comparison_turn(
    stage_id: str,
    workspace_id: str,
    run: dict[str, Any],
) -> None:
    state = run.get("state_summary", {}).get("task_state")
    revision = state.get("revision") if isinstance(state, dict) else None
    if not isinstance(revision, int):
        return
    state_file = managed_task_state_file(stage_id, workspace_id)
    target_revision = revision - 1
    if target_revision < 1:
        state_file.unlink(missing_ok=True)
        return
    revision_file = (
        managed_task_state_directory(stage_id, workspace_id)
        / "revisions"
        / f"revision_{target_revision}.json"
    )
    if not revision_file.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"Managed task state revision {target_revision} is unavailable.",
        )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(revision_file, state_file)


def find_instruction(stage_id: str) -> tuple[Path, str] | None:
    """Find the installed handout without duplicating its Markdown in stage metadata."""
    stage_number = stage_id.removeprefix("lab_")
    if not stage_number.isdigit():
        return None
    pattern = f"lab-{int(stage_number):02d}-*.md"
    roots = (
        (ROOT_DIR / "instructions", "instructions"),
        (ROOT_DIR / "student-labs", "student-labs"),
        (ROOT_DIR.parent / "student-labs", "student-labs"),
    )
    for root, source in roots:
        matches = sorted(root.glob(pattern))
        if matches:
            return matches[0], source
    return None


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
    message = str(exc) or type(exc).__name__
    try:
        api_keys = load_api_keys().values()
    except Exception:
        api_keys = ()
    for api_key in api_keys:
        if api_key:
            message = message.replace(api_key, "[REDACTED]")

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
