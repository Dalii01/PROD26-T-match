from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse
from sqlalchemy import select

from app.api.router import api_router
from app.db.database import AsyncSessionLocal, get_session
from app.db.config import settings
from app.metrics import get_latency_metrics, metrics_middleware, metrics_response
from app.model.baseline import MLRecommender
from app.models.user import User

_recommender = MLRecommender()

_recommender = MLRecommender()

app = FastAPI(title="t-match API")
app.state.settings = settings
app.include_router(api_router)

_ADMIN_DIR = Path(__file__).resolve().parent.parent / "admin-frontend"
_ADMIN_INDEX = _ADMIN_DIR / "index.html"
if _ADMIN_DIR.exists():
    app.mount("/panel", StaticFiles(directory=_ADMIN_DIR), name="admin-panel")

_SKIP_AUTH_PREFIXES = (
    "/health",
    "/metrics",
    "/users",
    "/panel",
    "/docs",
    "/redoc",
    "/openapi.json",
)


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

    if get_session not in request.app.dependency_overrides:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User.is_active).where(User.id == request.state.user_id)
            )
            is_active = result.scalar_one_or_none()
            if is_active is False:
                return JSONResponse(
                    status_code=403, content={"detail": "User is blocked"}
                )

    return await call_next(request)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    return await metrics_middleware(request, call_next)


@app.get("/panel")
async def admin_panel_index() -> FileResponse:
    return FileResponse(_ADMIN_INDEX)


@app.get("/panel/{path:path}")
async def admin_panel_assets(path: str) -> FileResponse:
    candidate = _ADMIN_DIR / path
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(_ADMIN_INDEX)


@app.get("/health")
async def health() -> dict:
    latency = await get_latency_metrics()
    return {"status": "ok", **_recommender.get_info(), **latency}


@app.get("/metrics")
async def metrics():
    return metrics_response()
