from fastapi import APIRouter

from app.schemas import StatsResponse
from app.services import get_stats


router = APIRouter(tags=["stats"])


@router.get("/api/stats", response_model=StatsResponse)
async def get_stats_route() -> StatsResponse:
    return await get_stats()
