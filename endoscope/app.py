import json
import uuid
from datetime import datetime, timezone

import aioboto3
from botocore.config import Config as BotoConfig
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from .config import EndoscopeConfig


def _s3_session(cfg: EndoscopeConfig):
    return aioboto3.Session()


def _s3_client(cfg: EndoscopeConfig, session: aioboto3.Session):
    return session.client(
        "s3",
        endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        region_name=cfg.s3_region,
        config=BotoConfig(signature_version="s3v4"),
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cfg: EndoscopeConfig = request.app.state.cfg
        if cfg.api_key:
            if request.headers.get("x-api-key") != cfg.api_key:
                return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return await call_next(request)


async def healthz(request: Request):
    return PlainTextResponse("ok")


async def readyz(request: Request):
    return PlainTextResponse("ready")


async def create_session(request: Request):
    cfg: EndoscopeConfig = request.app.state.cfg
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
    if cfg.s3_bucket:
        session_obj = _s3_session(cfg)
        async with _s3_client(cfg, session_obj) as s3:
            await s3.put_object(
                Bucket=cfg.s3_bucket,
                Key=f"{session_id}/metadata.json",
                Body=json.dumps(session).encode(),
            )
    return JSONResponse(session, status_code=201)


async def add_event(request: Request):
    cfg: EndoscopeConfig = request.app.state.cfg
    session_id = request.path_params["session_id"]
    if not cfg.s3_bucket:
        return JSONResponse({"error": "S3 bucket not configured"}, status_code=500)
    session_obj = _s3_session(cfg)
    async with _s3_client(cfg, session_obj) as s3:
        try:
            obj = await s3.get_object(
                Bucket=cfg.s3_bucket, Key=f"{session_id}/metadata.json"
            )
            session = json.loads(await obj["Body"].read())
        except Exception:
            return JSONResponse({"error": "session not found"}, status_code=404)
        data = await request.json()
        session.setdefault("events", []).append(data)
        await s3.put_object(
            Bucket=cfg.s3_bucket,
            Key=f"{session_id}/metadata.json",
            Body=json.dumps(session).encode(),
        )
    return JSONResponse({"status": "ok"})


async def add_file(request: Request):
    """Generate a signed S3 URL for direct upload.

    Returns ``upload_url`` JSON. Records expected file location in session.
    """
    cfg: EndoscopeConfig = request.app.state.cfg
    session_id = request.path_params["session_id"]
    if not cfg.s3_bucket:
        return JSONResponse({"error": "S3 bucket not configured"}, status_code=500)
    session_obj = _s3_session(cfg)
    async with _s3_client(cfg, session_obj) as s3:
        try:
            obj = await s3.get_object(
                Bucket=cfg.s3_bucket, Key=f"{session_id}/metadata.json"
            )
            session = json.loads(await obj["Body"].read())
        except Exception:
            return JSONResponse({"error": "session not found"}, status_code=404)
        form = await request.form()
        filename = form.get("filename")
        if not filename:
            file = form.get("file")
            filename = getattr(file, "filename", "upload.bin")
        s3_key = f"{session_id}/{filename}"
        try:
            upload_url = await s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": cfg.s3_bucket, "Key": s3_key},
                ExpiresIn=3600,
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        session.setdefault("files", []).append(
            {"filename": filename, "path": f"s3://{cfg.s3_bucket}/{s3_key}"}
        )
        await s3.put_object(
            Bucket=cfg.s3_bucket,
            Key=f"{session_id}/metadata.json",
            Body=json.dumps(session).encode(),
        )
    return JSONResponse({"upload_url": upload_url})


async def get_metadata(request: Request):
    cfg: EndoscopeConfig = request.app.state.cfg
    session_id = request.path_params["session_id"]
    if not cfg.s3_bucket:
        return JSONResponse({"error": "S3 bucket not configured"}, status_code=500)
    session_obj = _s3_session(cfg)
    async with _s3_client(cfg, session_obj) as s3:
        try:
            obj = await s3.get_object(
                Bucket=cfg.s3_bucket, Key=f"{session_id}/metadata.json"
            )
            sess = json.loads(await obj["Body"].read())
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


def create_app(cfg: EndoscopeConfig) -> Starlette:
    app = Starlette(
        debug=cfg.debug,
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/readyz", readyz, methods=["GET"]),
            Route("/v1/sessions", create_session, methods=["POST"]),
            Route("/v1/sessions/{session_id}/events", add_event, methods=["POST"]),
            Route("/v1/sessions/{session_id}/files", add_file, methods=["POST"]),
            Route("/v1/sessions/{session_id}/metadata", get_metadata, methods=["GET"]),
        ],
        middleware=[Middleware(AuthMiddleware)],
    )
    app.state.cfg = cfg
    return app
