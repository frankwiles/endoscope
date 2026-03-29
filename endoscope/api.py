import os
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, Any
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.datastructures import UploadFile
from pathlib import Path

# Simple in‑memory store for sessions
_sessions: Dict[str, Dict[str, Any]] = {}

# Authentication middleware
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        api_key = os.getenv("ENDO_API_KEY")
        if api_key:
            header = request.headers.get("x-api-key")
            if header != api_key:
                return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return await call_next(request)

async def healthz(request: Request):
    return PlainTextResponse("ok")

async def readyz(request: Request):
    return PlainTextResponse("ready")

async def create_session(request: Request):
    data = await request.json()
    project = data.get("project")
    session_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    session = {
        "session_id": session_id,
        "project": project,
        "timestamp": ts,
        "events": [],
        "files": [],
    }
    _sessions[session_id] = session
    return JSONResponse(session, status_code=201)

async def add_event(request: Request):
    session_id = request.path_params["session_id"]
    if session_id not in _sessions:
        return JSONResponse({"error": "session not found"}, status_code=404)
    data = await request.json()
    _sessions[session_id]["events"].append(data)
    return JSONResponse({"status": "ok"})

async def add_file(request: Request):
    session_id = request.path_params["session_id"]
    if session_id not in _sessions:
        return JSONResponse({"error": "session not found"}, status_code=404)
    form = await request.form()
    file = form.get("file")
    filename = form.get("filename") or getattr(file, "filename", "upload.bin")
    base_path = Path("data") / session_id
    base_path.mkdir(parents=True, exist_ok=True)
    path = base_path / filename
    # Ensure file is an UploadFile
    if isinstance(file, UploadFile):
        content = await file.read()
    else:
        content = b""
    with open(path, "wb") as f:
        f.write(content)
    _sessions[session_id]["files"].append({"filename": filename, "path": path})
    return JSONResponse({"status": "ok"})

async def get_manifest(request: Request):
    session_id = request.path_params["session_id"]
    sess = _sessions.get(session_id)
    if not sess:
        return JSONResponse({"error": "session not found"}, status_code=404)
    return JSONResponse(
        {
            "session_id": sess["session_id"],
            "project": sess["project"],
            "timestamp": sess["timestamp"],
            "events": sess["events"],
            "files": [f["filename"] for f in sess["files"]],
        }
    )

app = Starlette(
    debug=True,
    routes=[
        Route("/healthz", healthz, methods=["GET"]),
        Route("/readyz", readyz, methods=["GET"]),
        Route("/v1/sessions", create_session, methods=["POST"]),
        Route("/v1/sessions/{session_id}/events", add_event, methods=["POST"]),
        Route("/v1/sessions/{session_id}/files", add_file, methods=["POST"]),
        Route("/v1/sessions/{session_id}/manifest", get_manifest, methods=["GET"]),
    ],
    middleware=[Middleware(AuthMiddleware)],
)
