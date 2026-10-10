from fastapi import APIRouter, Depends

from app.auth import Principal, require_permission
from app.schemas import ChatRequest, ChatResponse
from app.services import build_chat_response


router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
async def chat_route(
    request: ChatRequest,
    _: Principal = Depends(require_permission("chat:use")),
) -> ChatResponse:
    return await build_chat_response(request)
