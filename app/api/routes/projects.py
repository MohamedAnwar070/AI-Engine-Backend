from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.auth import Principal, require_permission
from app.schemas import (
    MessageResponse,
    ProjectCreate,
    ProjectResponse,
    ProjectTestRequest,
    ProjectTestRouteResponse,
    ProjectTestRunResponse,
    WorkspaceImportResult,
)
from app.services import (
    create_project,
    delete_project,
    export_project_bundle,
    export_workspace_bundle,
    get_test_run,
    get_project,
    import_package,
    list_projects,
    run_project_tests,
    update_project,
)


router = APIRouter(tags=["projects"])


@router.post("/api/projects", response_model=ProjectResponse)
async def create_project_route(
    project: ProjectCreate,
    _: Principal = Depends(require_permission("projects:write")),
) -> ProjectResponse:
    return await create_project(project)


@router.get("/api/projects/{project_api_id}", response_model=ProjectResponse)
async def get_project_route(
    project_api_id: str,
    _: Principal = Depends(require_permission("projects:read")),
) -> ProjectResponse:
    return await get_project(project_api_id)


@router.get("/api/projects", response_model=List[ProjectResponse])
async def list_projects_route(
    _: Principal = Depends(require_permission("projects:read")),
) -> List[ProjectResponse]:
    return await list_projects()


@router.put("/api/projects/{project_api_id}", response_model=ProjectResponse)
async def update_project_route(
    project_api_id: str,
    project: ProjectCreate,
    _: Principal = Depends(require_permission("projects:write")),
) -> ProjectResponse:
    return await update_project(project_api_id, project)


@router.delete("/api/projects/{project_api_id}", response_model=MessageResponse)
async def delete_project_route(
    project_api_id: str,
    _: Principal = Depends(require_permission("projects:delete")),
) -> MessageResponse:
    return await delete_project(project_api_id)


@router.get("/api/projects/{project_api_id}/export")
async def export_project_route(
    project_api_id: str,
    _: Principal = Depends(require_permission("projects:import_export")),
) -> Dict[str, Any]:
    """Export a single project package (project + nodes + prompt)."""
    return await export_project_bundle(project_api_id)


@router.get("/api/workspace/export")
async def export_workspace_route(
    _: Principal = Depends(require_permission("projects:import_export")),
) -> Dict[str, Any]:
    """Export all projects as a workspace package."""
    return await export_workspace_bundle()


@router.post(
    "/api/packages/import",
    response_model=WorkspaceImportResult,
)
async def import_package_route(
    payload: Dict[str, Any],
    _: Principal = Depends(require_permission("projects:import_export")),
) -> WorkspaceImportResult:
    """
    Import v2 package (single or multi project).
    Creates project(s) with llm_prompt and all nodes (runtime/http_config preserved).
    """
    return await import_package(payload)


@router.post(
    "/api/projects/{project_api_id}/test", response_model=ProjectTestRouteResponse
)
async def test_project_route(
    project_api_id: str,
    test_request: ProjectTestRequest,
    _: Principal = Depends(require_permission("projects:test")),
) -> ProjectTestRouteResponse:
    return await run_project_tests(project_api_id, test_request)


@router.get("/api/test-runs/{test_run_id}", response_model=ProjectTestRunResponse)
async def get_test_run_route(
    test_run_id: str,
    _: Principal = Depends(require_permission("projects:test")),
) -> ProjectTestRunResponse:
    return await get_test_run(test_run_id)
