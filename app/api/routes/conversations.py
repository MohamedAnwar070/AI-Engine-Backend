from fastapi import APIRouter

from app.schemas import ConversationResponse
from app.services import get_conversation


router = APIRouter(tags=["conversations"])


@router.get("/api/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation_route(conversation_id: str) -> ConversationResponse:
    return await get_conversation(conversation_id)
