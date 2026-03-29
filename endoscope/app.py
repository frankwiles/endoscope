import os
import uuid
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

# Sessions are persisted in S3; no in‑memory store


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
    # Store manifest in S3
    import json
    import boto3
    s3 = boto3.client('s3')
    bucket = os.getenv('ENDO_S3_BUCKET')
    if bucket:
        s3.put_object(Bucket=bucket, Key=f"{session_id}/manifest.json", Body=json.dumps(session).encode())
    return JSONResponse(session, status_code=201)


async def add_event(request: Request):
    session_id = request.path_params["session_id"]
    # Load session manifest from S3
    import json
    import boto3
    s3 = boto3.client('s3')
    bucket = os.getenv('ENDO_S3_BUCKET')
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"{session_id}/manifest.json")
        session = json.loads(obj['Body'].read())
    except Exception:
        return JSONResponse({"error": "session not found"}, status_code=404)
    data = await request.json()
    session.setdefault('events', []).append(data)
    # Save updated manifest
    s3.put_object(Bucket=bucket, Key=f"{session_id}/manifest.json", Body=json.dumps(session).encode())
    return JSONResponse({"status": "ok"})


async def add_file(request: Request):
    """Generate a signed S3 URL for direct upload.

    Returns ``upload_url`` JSON. Records expected file location in session.
    """
    session_id = request.path_params["session_id"]
    # Load session manifest from S3
    import json
    import boto3
    s3 = boto3.client('s3')
    bucket = os.getenv('ENDO_S3_BUCKET')
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"{session_id}/manifest.json")
        session = json.loads(obj['Body'].read())
    except Exception:
        return JSONResponse({"error": "session not found"}, status_code=404)
    form = await request.form()
    filename = form.get("filename")
    if not filename:
        file = form.get("file")
        filename = getattr(file, "filename", "upload.bin")
    s3_key = f"{session_id}/{filename}"
    import boto3

    s3_client = boto3.client("s3")
    bucket = os.getenv("ENDO_S3_BUCKET")
    if not bucket:
        return JSONResponse({"error": "S3 bucket not configured"}, status_code=500)
    try:
        upload_url = s3_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": s3_key},
            ExpiresIn=3600,
        )
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    session.setdefault('files', []).append({"filename": filename, "path": f"s3://{bucket}/{s3_key}"})
    # Save updated manifest
    s3.put_object(Bucket=bucket, Key=f"{session_id}/manifest.json", Body=json.dumps(session).encode())
    return JSONResponse({"upload_url": upload_url})


async def get_manifest(request: Request):
    session_id = request.path_params["session_id"]
    # Load session manifest from S3
    import json
    import boto3
    s3 = boto3.client('s3')
    bucket = os.getenv('ENDO_S3_BUCKET')
    try:
        obj = s3.get_object(Bucket=bucket, Key=f"{session_id}/manifest.json")
        sess = json.loads(obj['Body'].read())
    except Exception:
        sess = None
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
