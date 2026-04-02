"""Starlette API for endoscope — debug artifact capture service.

All routes delegate to SessionService for business logic.
"""

from __future__ import annotations

import hmac
from pathlib import PurePath
from uuid import UUID

import structlog
from pydantic import ValidationError

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .config import EndoscopeConfig
from .services import (
    PruneRequest,
    SessionCreateRequest,
    SessionService,
    make_session_service,
    parse_duration,
)
from .storage import StorageError



log = structlog.get_logger()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cfg: EndoscopeConfig = request.app.state.cfg
        # Health endpoints bypass authentication.
        if request.url.path in ("/healthz", "/readyz"):
            return await call_next(request)
        if cfg.api_key:
            if not hmac.compare_digest(
                request.headers.get("x-api-key", ""), cfg.api_key
            ):
                return JSONResponse(
                    {"error": "unauthenticated"}, status_code=401
                )
        else:
            log.warning("auth.no_api_key", msg="API key not configured — all requests allowed")
        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _svc(request: Request) -> SessionService:
    return request.app.state.svc


def _cfg(request: Request) -> EndoscopeConfig:
    return request.app.state.cfg


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


async def healthz(request: Request):
    return PlainTextResponse("ok")


async def readyz(request: Request):
    """Readiness check — verifies S3 connectivity."""
    svc = _svc(request)
    try:
        ok = await svc.check_ready()
    except Exception:
        ok = False
    if ok:
        return PlainTextResponse("ready")
    return PlainTextResponse("not ready", status_code=503)


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


async def create_session(request: Request):
    cfg = _cfg(request)
    svc = _svc(request)
    data = await request.json()
    project = data.get("project", cfg.project)
    metadata = data.get("metadata")

    req = SessionCreateRequest(project=project, metadata=metadata)
    session = await svc.create_session(req)

    return JSONResponse(
        session.model_dump(mode="json"),
        status_code=201,
    )


async def list_sessions(request: Request):
    cfg = _cfg(request)
    svc = _svc(request)
    summaries = await svc.list_sessions(cfg.project)
    return JSONResponse([s.model_dump(mode="json") for s in summaries])


async def get_session(request: Request):
    svc = _svc(request)
    session_id = _parse_uuid(request.path_params["session_id"])
    if session_id is None:
        return JSONResponse({"error": "invalid session id"}, status_code=400)

    session = await svc.get_session(session_id)
    if session is None:
        return JSONResponse({"error": "session not found"}, status_code=404)

    return JSONResponse(session.model_dump(mode="json"))


async def delete_session(request: Request):
    svc = _svc(request)
    session_id = _parse_uuid(request.path_params["session_id"])
    if session_id is None:
        return JSONResponse({"error": "invalid session id"}, status_code=400)

    deleted = await svc.delete_session(session_id)
    if not deleted:
        return JSONResponse({"error": "session not found"}, status_code=404)

    return JSONResponse({"deleted": str(session_id)})


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def add_event(request: Request):
    svc = _svc(request)
    session_id = _parse_uuid(request.path_params["session_id"])
    if session_id is None:
        return JSONResponse({"error": "invalid session id"}, status_code=400)

    event = await request.json()
    session = await svc.add_event(session_id, event=event)
    if session is None:
        return JSONResponse({"error": "session not found"}, status_code=404)

    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


async def add_file(request: Request):
    """Generate a signed S3 URL for direct upload.

    Returns ``upload_url`` JSON. Records expected file location in session.
    """
    svc = _svc(request)
    session_id = _parse_uuid(request.path_params["session_id"])
    if session_id is None:
        return JSONResponse({"error": "invalid session id"}, status_code=400)

    form = await request.form()
    filename = form.get("filename")
    if not filename:
        file = form.get("file")
        filename = getattr(file, "filename", "upload.bin")

    try:
        result = await svc.add_file(session_id, filename=filename)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if result is None:
        return JSONResponse({"error": "session not found"}, status_code=404)

    _session, upload_url = result
    return JSONResponse({"upload_url": upload_url})


async def download_file(request: Request):
    """Download a file attached to a session."""
    svc = _svc(request)
    session_id = _parse_uuid(request.path_params["session_id"])
    if session_id is None:
        return JSONResponse({"error": "invalid session id"}, status_code=400)

    # Sanitize filename to prevent path traversal attacks
    filename = PurePath(request.path_params["filename"]).name
    data = await svc.get_file_bytes(session_id, filename=filename)
    if data is None:
        return JSONResponse({"error": "file not found"}, status_code=404)

    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------


async def prune_sessions(request: Request):
    cfg = _cfg(request)
    svc = _svc(request)
    data = await request.json()

    try:
        req = PruneRequest.model_validate(data)
    except ValidationError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if req.all:
        count = await svc.prune_sessions(cfg.project, all=True)
    elif req.older_than:
        try:
            older_than = parse_duration(req.older_than)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        count = await svc.prune_sessions(cfg.project, older_than=older_than)
    else:
        return JSONResponse(
            {"error": "provide 'older_than' or 'all'"},
            status_code=400,
        )

    return JSONResponse({"pruned": count})


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(cfg: EndoscopeConfig) -> Starlette:
    svc = make_session_service(cfg)

    async def _storage_error_handler(
        request: Request, exc: StorageError
    ) -> JSONResponse:
        log.error("storage.error", error=str(exc))
        return JSONResponse({"error": "internal server error"}, status_code=500)

    app = Starlette(
        debug=cfg.debug,
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/readyz", readyz, methods=["GET"]),
            Route("/v1/sessions", create_session, methods=["POST"]),
            Route("/v1/sessions", list_sessions, methods=["GET"]),
            Route(
                "/v1/sessions/{session_id}", get_session, methods=["GET"]
            ),
            Route(
                "/v1/sessions/{session_id}",
                delete_session,
                methods=["DELETE"],
            ),
            Route(
                "/v1/sessions/{session_id}/events",
                add_event,
                methods=["POST"],
            ),
            Route(
                "/v1/sessions/{session_id}/files",
                add_file,
                methods=["POST"],
            ),
            Route(
                "/v1/sessions/{session_id}/files/{filename}",
                download_file,
                methods=["GET"],
            ),
            Route("/v1/prune", prune_sessions, methods=["POST"]),
            # Legacy alias — kept for backward compat
            Route(
                "/v1/sessions/{session_id}/metadata",
                get_session,
                methods=["GET"],
            ),
        ],
        middleware=[Middleware(AuthMiddleware)],
        exception_handlers={StorageError: _storage_error_handler},
    )
    app.state.cfg = cfg
    app.state.svc = svc
    return app
