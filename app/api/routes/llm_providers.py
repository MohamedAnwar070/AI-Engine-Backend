from typing import List

from fastapi import APIRouter, Depends

from app.auth import Principal, require_permission
from app.core.llm_catalog import get_catalog
from app.schemas import (
    LLMProviderCreate,
    LLMProviderProjectBinding,
    LLMProviderResponse,
    LLMProviderTestResponse,
    LLMProviderUpdate,
    ProjectResponse,
)
from app.services_llm_providers import (
    bind_project_to_provider,
    create_llm_provider,
    delete_llm_provider,
    get_llm_provider,
    list_llm_providers,
    list_provider_projects,
    set_default_llm_provider,
    test_llm_provider,
    test_unsaved_llm_config,
    unbind_project_from_provider,
    update_llm_provider,
)

router = APIRouter(tags=["llm-providers"])


@router.get("/api/llm-providers/catalog")
async def llm_catalog_route(
    _: Principal = Depends(require_permission("providers:read")),
):
    """Static presets (Gemini/Groq/OpenRouter/GitHub/OpenAI/custom) + doc links.

    Free-tier numbers are NOT hardcoded here — they change per account/region.
    Each entry has limits_note + docs_url; record verified quotas on the DB doc.
    """
    return {"providers": get_catalog()}


@router.get("/api/llm-providers", response_model=List[LLMProviderResponse])
async def list_llm_providers_route(
    _: Principal = Depends(require_permission("providers:read")),
) -> List[LLMProviderResponse]:
    return await list_llm_providers()


@router.post("/api/llm-providers", response_model=LLMProviderResponse)
async def create_llm_provider_route(
    payload: LLMProviderCreate,
    _: Principal = Depends(require_permission("providers:manage")),
) -> LLMProviderResponse:
    return await create_llm_provider(payload)


@router.post("/api/llm-providers/test-connection", response_model=LLMProviderTestResponse)
async def test_unsaved_llm_provider_route(
    payload: LLMProviderCreate,
    _: Principal = Depends(require_permission("providers:manage")),
) -> LLMProviderTestResponse:
    return await test_unsaved_llm_config(payload)


@router.get("/api/llm-providers/{provider_id}", response_model=LLMProviderResponse)
async def get_llm_provider_route(
    provider_id: str,
    _: Principal = Depends(require_permission("providers:read")),
) -> LLMProviderResponse:
    return await get_llm_provider(provider_id)


@router.put("/api/llm-providers/{provider_id}", response_model=LLMProviderResponse)
async def update_llm_provider_route(
    provider_id: str,
    payload: LLMProviderUpdate,
    _: Principal = Depends(require_permission("providers:manage")),
) -> LLMProviderResponse:
    return await update_llm_provider(provider_id, payload)


@router.delete("/api/llm-providers/{provider_id}")
async def delete_llm_provider_route(
    provider_id: str,
    _: Principal = Depends(require_permission("providers:manage")),
):
    await delete_llm_provider(provider_id)
    return {"message": "LLM provider deleted"}


@router.post("/api/llm-providers/{provider_id}/set-default", response_model=LLMProviderResponse)
async def set_default_llm_provider_route(
    provider_id: str,
    _: Principal = Depends(require_permission("providers:manage")),
) -> LLMProviderResponse:
    return await set_default_llm_provider(provider_id)


@router.post("/api/llm-providers/{provider_id}/test", response_model=LLMProviderTestResponse)
async def test_llm_provider_route(
    provider_id: str,
    _: Principal = Depends(require_permission("providers:manage")),
) -> LLMProviderTestResponse:
    return await test_llm_provider(provider_id)


@router.get("/api/llm-providers/{provider_id}/models")
async def list_llm_provider_models_route(
    provider_id: str,
    _: Principal = Depends(require_permission("providers:read")),
):
    """Discover model ids from a custom OpenAI-compatible endpoint."""
    from app.services_llm_providers import list_models_for_provider

    return {"provider_id": provider_id, "models": await list_models_for_provider(provider_id)}


@router.get("/api/llm-providers/{provider_id}/projects", response_model=List[ProjectResponse])
async def list_llm_provider_projects_route(
    provider_id: str,
    _: Principal = Depends(require_permission("providers:read")),
) -> List[ProjectResponse]:
    """Projects pinned to this provider (their chat uses ONLY this provider)."""
    return await list_provider_projects(provider_id)


@router.post("/api/llm-providers/{provider_id}/projects", response_model=ProjectResponse)
async def bind_llm_provider_project_route(
    provider_id: str,
    payload: LLMProviderProjectBinding,
    _: Principal = Depends(require_permission("providers:manage")),
) -> ProjectResponse:
    return await bind_project_to_provider(provider_id, payload.project_api_id)


@router.delete("/api/llm-providers/{provider_id}/projects/{project_api_id}", response_model=ProjectResponse)
async def unbind_llm_provider_project_route(
    provider_id: str,
    project_api_id: str,
    _: Principal = Depends(require_permission("providers:manage")),
) -> ProjectResponse:
    return await unbind_project_from_provider(provider_id, project_api_id)
