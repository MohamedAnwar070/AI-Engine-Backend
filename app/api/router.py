from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.execution import router as execution_router
from app.api.routes.intelligence import router as intelligence_router
from app.api.routes.llm_providers import router as llm_providers_router
from app.api.routes.nodes import router as nodes_router
from app.api.routes.projects import router as projects_router
from app.api.routes.stats import router as stats_router


api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(stats_router)
api_router.include_router(projects_router)
api_router.include_router(nodes_router)
api_router.include_router(execution_router)
api_router.include_router(intelligence_router)
api_router.include_router(chat_router)
api_router.include_router(conversations_router)
api_router.include_router(llm_providers_router)
