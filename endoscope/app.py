from contextlib import asynccontextmanager

import environs

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import structlog

from endoscope.services import SessionCreateRequest, SessionService, make_session_service


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_API_KEY = environs.Env().str("ENDO_API_KEY", "")


def _check_auth(request: Request) -> JSONResponse | None:
    if not _API_KEY:
        return None
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip()
    if token == _API_KEY:
        return None
    return JSONResponse(
        {"error": "unauthenticated", "detail": "Invalid or missing API key"},
        status_code=401,
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def healthz(request: Request):
    log.info("healthz.ok")
    return JSONResponse({"status": "ok"})


async def readyz(request: Request):
    log.info("readyz.ok")
    return JSONResponse({"status": "ready"})


log = structlog.get_logger("endoscope.app")

async def create_session(request: Request):
    if err := _check_auth(request):
        log.warning("session.create.auth_failed")
        return err

    svc: SessionService = request.app.state.session_service
    body = await request.json()
    req = SessionCreateRequest.model_validate(body)
    session = await svc.create_session(req)
    log.info(
        "session.create.ok",
        session_id=str(session.session_id),
        project=session.project,
    )
    return JSONResponse(session.model_dump(mode="json"), status_code=201)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: Starlette):
    from endoscope.logging import configure

    configure()
    log = structlog.get_logger("endoscope.app")
    log.info("app.starting")
    app.state.session_service = make_session_service()
    yield
    log.info("app.stopping")


routes = [
    Route("/healthz", healthz),
    Route("/readyz", readyz),
    Route("/v1/sessions", create_session, methods=["POST"]),
]

app = Starlette(routes=routes, lifespan=_lifespan)