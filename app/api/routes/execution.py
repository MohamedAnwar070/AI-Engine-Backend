from typing import List

from fastapi import APIRouter, Query

from app.schemas import (
    ExecuteNodeRequest,
    ExecutionHistoryResponse,
    NodeExecutionResponse,
)
from app.services import execute_named_node, list_execution_history


router = APIRouter(tags=["execution"])


@router.post("/api/execute-node", response_model=NodeExecutionResponse)
async def execute_node_route(request: ExecuteNodeRequest) -> NodeExecutionResponse:
    return await execute_named_node(
        request.project_api_id,
        request.node_name,
        request.input_data,
        timeout_ms=request.timeout_ms,
        cache_ttl_seconds=request.cache_ttl_seconds,
        max_retries=request.max_retries,
    )


@router.get(
    "/api/projects/{project_api_id}/executions",
    response_model=List[ExecutionHistoryResponse],
)
async def list_execution_history_route(
    project_api_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> List[ExecutionHistoryResponse]:
    return await list_execution_history(project_api_id, limit=limit)
