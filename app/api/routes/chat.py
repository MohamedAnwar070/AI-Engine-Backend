from fastapi import APIRouter

from app.schemas import ChatRequest, ChatResponse
from app.services import build_chat_response


router = APIRouter(tags=["chat"])


@router.post("/api/chat", response_model=ChatResponse)
async def chat_route(request: ChatRequest) -> ChatResponse:
    return await build_chat_response(request)
