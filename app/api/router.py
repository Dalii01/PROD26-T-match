from fastapi import APIRouter

from app.api.routers import (
    audit_log,
    blocks,
    conversations,
    interactions,
    matches,
    rank,
    recommendations,
    reports,
    users,
)

api_router = APIRouter()

api_router.include_router(audit_log.router, prefix="/audit-log", tags=["audit-log"])
api_router.include_router(blocks.router, prefix="/blocks", tags=["blocks"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(
    conversations.router, prefix="/conversations", tags=["conversations"]
)
api_router.include_router(
    interactions.router, prefix="/interactions", tags=["interactions"]
)
api_router.include_router(matches.router, prefix="/matches", tags=["matches"])
api_router.include_router(
    recommendations.router, prefix="/recommendations", tags=["recommendations"]
)
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.add_api_route(
    "/reject",
    reports.reject_report,
    methods=["POST"],
    tags=["reports"],
)
api_router.include_router(rank.router, tags=["rank"])
