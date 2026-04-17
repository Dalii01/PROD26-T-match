from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.routers.rank import router as rank_router
from app.db.config import settings
from app.metrics import metrics_middleware, metrics_response

app = FastAPI(title="t-match ML API")
app.state.settings = settings
app.include_router(rank_router)

_SKIP_AUTH_PREFIXES = ("/health", "/metrics", "/docs", "/redoc", "/openapi.json")


@app.middleware("http")
async def user_header_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path.startswith(_SKIP_AUTH_PREFIXES):
        return await call_next(request)

    raw_user_id = request.headers.get("X-User-ID")
    if not raw_user_id:
        return JSONResponse(
            status_code=400, content={"detail": "X-User-ID header required"}
        )
    try:
        request.state.user_id = int(raw_user_id)
    except ValueError:
        return JSONResponse(
            status_code=400, content={"detail": "X-User-ID must be integer"}
        )

    return await call_next(request)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    return await metrics_middleware(request, call_next)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return metrics_response()
