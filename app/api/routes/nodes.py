from typing import List

from fastapi import APIRouter, Depends

from app.auth import require_roles
from app.schemas import MessageResponse, NodeCreate, NodeResponse
from app.services import (
    create_node,
    delete_node,
    get_node,
    list_nodes,
    rollback_node_version,
    update_node,
)


router = APIRouter(tags=["nodes"])


@router.post("/api/nodes", response_model=NodeResponse)
async def create_node_route(
    node: NodeCreate, _: dict = Depends(require_roles("admin", "editor"))
) -> NodeResponse:
    return await create_node(node)


@router.get("/api/nodes/{node_id}", response_model=NodeResponse)
async def get_node_route(node_id: str) -> NodeResponse:
    return await get_node(node_id)


@router.get("/api/projects/{project_api_id}/nodes", response_model=List[NodeResponse])
async def list_nodes_route(project_api_id: str) -> List[NodeResponse]:
    return await list_nodes(project_api_id)


@router.put("/api/nodes/{node_id}", response_model=NodeResponse)
async def update_node_route(
    node_id: str, node: NodeCreate, _: dict = Depends(require_roles("admin", "editor"))
) -> NodeResponse:
    return await update_node(node_id, node)


@router.delete("/api/nodes/{node_id}", response_model=MessageResponse)
async def delete_node_route(
    node_id: str, _: dict = Depends(require_roles("admin"))
) -> MessageResponse:
    return await delete_node(node_id)


@router.post(
    "/api/projects/{project_api_id}/nodes/{node_id}/rollback/{version_id}",
    response_model=NodeResponse,
)
async def rollback_node_version_route(
    project_api_id: str,
    node_id: str,
    version_id: str,
    _: dict = Depends(require_roles("admin", "editor")),
) -> NodeResponse:
    return await rollback_node_version(project_api_id, node_id, version_id)
