from fastapi import APIRouter, Depends

from app.auth import get_current_user, login_user, register_user, require_roles
from app.schemas import AuthLoginRequest, TokenResponse, UserCreate, UserResponse


router = APIRouter(tags=["auth"])


@router.post("/api/auth/login", response_model=TokenResponse)
async def login_route(credentials: AuthLoginRequest) -> TokenResponse:
    return await login_user(credentials)


@router.post("/api/auth/register", response_model=UserResponse)
async def register_route(
    user: UserCreate, actor: dict = Depends(require_roles("admin"))
) -> UserResponse:
    return await register_user(user, actor=actor)


@router.get("/api/auth/me", response_model=UserResponse)
async def me_route(user: dict = Depends(get_current_user)) -> UserResponse:
    user = dict(user)
    user["_id"] = str(user["_id"])
    return UserResponse.model_validate(user)
