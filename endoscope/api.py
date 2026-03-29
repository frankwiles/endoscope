import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.middleware.base import BaseHTTPMiddleware

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
    """Generate a signed S3 URL for direct upload.

    The client provides a ``filename`` (or the uploaded file's original
    name).  Instead of streaming the file through the API we create a
    presigned URL that the client can POST to.  The URL is returned in the
    ``upload_url`` field of the JSON response.  We also record the expected
    file location in the session metadata so that later calls can retrieve
    the manifest.
    """
    session_id = request.path_params["session_id"]
    if session_id not in _sessions:
        return JSONResponse({"error": "session not found"}, status_code=404)
    form = await request.form()
    filename = form.get("filename")
    if not filename:
        file = form.get("file")
        filename = getattr(file, "filename", "upload.bin")
    # Build the S3 key where the file will be stored
    s3_key = f"{session_id}/{filename}"
    # Use boto3 to generate a presigned URL for a PUT operation
    import boto3
    s3_client = boto3.client('s3')
    bucket = os.getenv('ENDO_S3_BUCKET')
    if not bucket:
        return JSONResponse({"error": "S3 bucket not configured"}, status_code=500)
    try:
        upload_url = s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': bucket, 'Key': s3_key},
            ExpiresIn=3600,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    # Record the expected file location (path is stored for manifest purposes)
    _sessions[session_id]["files"].append({"filename": filename, "path": f"s3://{bucket}/{s3_key}"})
    return JSONResponse({"upload_url": upload_url})

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
