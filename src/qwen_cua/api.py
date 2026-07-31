from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, TypeVar

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .models import (
    ApprovalRequest,
    ModelInfo,
    RunDetail,
    RunEvent,
    ScenarioManifest,
    StartRunRequest,
    StartRunResponse,
    UserInputRequest,
)
from .runner import RunnerManager

T = TypeVar("T")


def create_app(
    settings: Settings | None = None,
    manager: RunnerManager | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_manager = manager or RunnerManager(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await resolved_manager.close()

    app = FastAPI(
        title="Qwen CUA Runner",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.manager = resolved_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            resolved_settings.web_origin,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type", "last-event-id"],
    )
    if resolved_settings.labs_root.is_dir():
        app.mount(
            "/labs",
            StaticFiles(directory=resolved_settings.labs_root, html=True),
            name="labs",
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "qwen-cua-runner"}

    @app.get("/api/models", response_model=list[ModelInfo])
    async def models() -> list[ModelInfo]:
        return resolved_manager.models()

    @app.get("/api/scenarios", response_model=list[ScenarioManifest])
    async def scenarios() -> list[ScenarioManifest]:
        return resolved_manager.scenarios()

    @app.post("/api/runs", response_model=StartRunResponse, status_code=202)
    async def start_run(request: StartRunRequest) -> StartRunResponse:
        return await _translate_errors(resolved_manager.start_run(request))

    @app.get("/api/runs/{run_id}", response_model=RunDetail)
    async def get_run(run_id: str) -> RunDetail:
        return await _translate_errors(resolved_manager.get_run(run_id))

    @app.post("/api/runs/{run_id}/stop", response_model=RunDetail)
    async def stop_run(run_id: str) -> RunDetail:
        return await _translate_errors(resolved_manager.stop_run(run_id))

    @app.get("/api/runs/{run_id}/events/snapshot", response_model=list[RunEvent])
    async def event_snapshot(run_id: str) -> list[RunEvent]:
        return await _translate_errors(resolved_manager.get_events(run_id))

    @app.get("/api/runs/{run_id}/events")
    async def events(
        run_id: str,
        last_event_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        try:
            after = int(last_event_id.rsplit(":", 1)[-1]) if last_event_id else -1
            resolved_manager._context(run_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        async def stream() -> AsyncIterator[str]:
            async for event in resolved_manager.wait_for_events(
                run_id,
                after_sequence=after,
            ):
                if event is None:
                    yield ": heartbeat\n\n"
                else:
                    yield (f"id: {event.id}\ndata: {event.model_dump_json()}\n\n")

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/runs/{run_id}/replay")
    async def replay(run_id: str) -> JSONResponse:
        payload = await _translate_errors(resolved_manager.replay(run_id))
        return JSONResponse(payload)

    @app.post(
        "/api/runs/{run_id}/approvals/{intervention_id}",
        response_model=RunDetail,
    )
    async def resolve_approval(
        run_id: str,
        intervention_id: str,
        request: ApprovalRequest,
    ) -> RunDetail:
        return await _translate_errors(
            resolved_manager.resolve_approval(run_id, intervention_id, request)
        )

    @app.post("/api/runs/{run_id}/user-input", response_model=RunDetail)
    async def user_input(run_id: str, request: UserInputRequest) -> RunDetail:
        return await _translate_errors(resolved_manager.resolve_user_input(run_id, request))

    @app.post(
        "/api/runs/{run_id}/interventions/{intervention_id}/file",
        response_model=RunDetail,
    )
    async def upload_file(
        run_id: str,
        intervention_id: str,
        file: Annotated[UploadFile, File()],
    ) -> RunDetail:
        payload = await file.read(resolved_settings.max_upload_bytes + 1)
        return await _translate_errors(
            resolved_manager.resolve_file_upload(
                run_id,
                intervention_id,
                filename=file.filename or "upload.bin",
                payload=payload,
            )
        )

    @app.get("/api/runs/{run_id}/artifacts/screenshots/{filename}")
    async def screenshot(run_id: str, filename: str) -> FileResponse:
        path = _artifact_path(lambda: resolved_manager.screenshot_path(run_id, filename))
        return FileResponse(path, media_type="image/png")

    @app.get("/api/runs/{run_id}/artifacts/downloads/{filename}")
    async def download(run_id: str, filename: str) -> FileResponse:
        path = _artifact_path(lambda: resolved_manager.download_path(run_id, filename))
        return FileResponse(path, filename=path.name)

    return app


async def _translate_errors(awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _artifact_path(resolver: Callable[[], Path]) -> Path:
    try:
        return resolver()
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


app = create_app()
