from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.auth import require_roles
from app.schemas import (
    ControlPlaneOverviewResponse,
    ConversationRecoveryResponse,
    GovernanceAuditResponse,
    NodeExecutionResponse,
    NodeGraphResponse,
    NodeVersionResponse,
    ProjectTemplateCreate,
    ProjectTemplateUpdate,
    ProjectTemplateResponse,
    PromptPresetCreate,
    PromptPresetResponse,
    ReplayExecutionRequest,
    SimulationResponse,
    ToolAuditResponse,
)
from app.services import (
    create_prompt_preset,
    create_project_template,
    get_control_plane_overview,
    get_project_graph,
    list_governance_audits,
    list_node_versions,
    list_project_templates,
    list_prompt_presets,
    list_tool_audits,
    receive_alert_webhook,
    recover_conversation,
    replay_execution,
    simulate_project_graph,
    update_project_template,
)


router = APIRouter(tags=["intelligence"])


@router.get("/api/templates/projects", response_model=List[ProjectTemplateResponse])
async def list_project_templates_route() -> List[ProjectTemplateResponse]:
    return await list_project_templates()


@router.post("/api/templates/projects", response_model=ProjectTemplateResponse)
async def create_project_template_route(
    template: ProjectTemplateCreate, _: dict = Depends(require_roles("admin", "editor"))
) -> ProjectTemplateResponse:
    return await create_project_template(template)


@router.put(
    "/api/templates/projects/{template_id}", response_model=ProjectTemplateResponse
)
async def update_project_template_route(
    template_id: str,
    template: ProjectTemplateUpdate,
    _: dict = Depends(require_roles("admin", "editor")),
) -> ProjectTemplateResponse:
    return await update_project_template(template_id, template)


@router.get(
    "/api/projects/{project_api_id}/presets", response_model=List[PromptPresetResponse]
)
async def list_prompt_presets_route(project_api_id: str) -> List[PromptPresetResponse]:
    return await list_prompt_presets(project_api_id)


@router.post(
    "/api/projects/{project_api_id}/presets", response_model=PromptPresetResponse
)
async def create_prompt_preset_route(
    project_api_id: str,
    preset: PromptPresetCreate,
    _: dict = Depends(require_roles("admin", "editor")),
) -> PromptPresetResponse:
    return await create_prompt_preset(project_api_id, preset)


@router.get("/api/nodes/{node_id}/versions", response_model=List[NodeVersionResponse])
async def list_node_versions_route(node_id: str) -> List[NodeVersionResponse]:
    return await list_node_versions(node_id)


@router.get("/api/projects/{project_api_id}/graph", response_model=NodeGraphResponse)
async def get_project_graph_route(project_api_id: str) -> NodeGraphResponse:
    return await get_project_graph(project_api_id)


@router.get(
    "/api/projects/{project_api_id}/simulate", response_model=SimulationResponse
)
async def simulate_project_route(project_api_id: str) -> SimulationResponse:
    return await simulate_project_graph(project_api_id)


@router.post(
    "/api/executions/{execution_id}/replay", response_model=NodeExecutionResponse
)
async def replay_execution_route(
    execution_id: str,
    request: ReplayExecutionRequest,
    _: dict = Depends(require_roles("admin", "editor")),
) -> NodeExecutionResponse:
    return await replay_execution(execution_id, request)


@router.get(
    "/api/projects/{project_api_id}/audits", response_model=List[ToolAuditResponse]
)
async def list_tool_audits_route(
    project_api_id: str, limit: int = Query(default=100, ge=1, le=200)
) -> List[ToolAuditResponse]:
    return await list_tool_audits(project_api_id, limit=limit)


@router.get(
    "/api/projects/{project_api_id}/recovery",
    response_model=ConversationRecoveryResponse,
)
async def recover_conversation_route(
    project_api_id: str, session_id: Optional[str] = None
) -> ConversationRecoveryResponse:
    return await recover_conversation(project_api_id, session_id)


@router.get("/api/control-plane/overview", response_model=ControlPlaneOverviewResponse)
async def control_plane_overview_route() -> ControlPlaneOverviewResponse:
    return await get_control_plane_overview()


@router.get("/api/governance/audits", response_model=List[GovernanceAuditResponse])
async def governance_audits_route(
    limit: int = Query(default=200, ge=1, le=500),
    _: dict = Depends(require_roles("admin")),
) -> List[GovernanceAuditResponse]:
    return await list_governance_audits(limit=limit)


@router.post("/api/alerts/webhook")
async def alerts_webhook_route(payload: dict):
    return await receive_alert_webhook(payload)
