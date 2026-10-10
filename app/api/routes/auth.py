from fastapi import APIRouter, Depends

from app.auth import Principal, get_current_principal, login_admin, require_permission, to_user_response
from app.schemas import AuthLoginRequest, TokenResponse, UserResponse


router = APIRouter(tags=["auth"])


@router.post("/api/auth/login", response_model=TokenResponse)
async def login_route(credentials: AuthLoginRequest) -> TokenResponse:
    return await login_admin(credentials)


@router.get("/api/auth/me", response_model=UserResponse)
async def me_route(
    principal: Principal = Depends(require_permission("auth:me")),
) -> UserResponse:
    return to_user_response(principal)
