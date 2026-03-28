from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def healthz(request):
    return JSONResponse({"status": "ok"})


async def readyz(request):
    return JSONResponse({"status": "ready"})


routes = [
    Route("/healthz", healthz),
    Route("/readyz", readyz),
]

app = Starlette(routes=routes)
