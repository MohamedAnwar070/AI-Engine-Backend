from fastapi import APIRouter, Depends

from app.auth import Principal, require_permission
from app.schemas import StatsResponse
from app.services import get_stats


router = APIRouter(tags=["stats"])


@router.get("/api/stats", response_model=StatsResponse)
async def get_stats_route(
    _: Principal = Depends(require_permission("stats:read")),
) -> StatsResponse:
    return await get_stats()
