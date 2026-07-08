"""Login / token endpoints.

``/access-token`` is form-encoded (``OAuth2PasswordRequestForm``), NOT JSON — the
OAuth2 password flow the interactive docs' Authorize button drives.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm

from backend import crud
from backend.api.deps import CurrentUser, SessionDep
from backend.config import settings
from backend.core.security import create_access_token
from backend.db.models import Token, User, UserPublic

router = APIRouter(prefix="/login", tags=["login"])


@router.post("/access-token")
async def login_access_token(
    session: SessionDep,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> Token:
    user = await crud.authenticate(
        session, email=form_data.username, password=form_data.password
    )
    if user is None:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    access_token = create_access_token(
        user.id, timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return Token(access_token=access_token)


@router.post("/test-token", response_model=UserPublic)
async def test_token(current_user: CurrentUser) -> User:
    """Validate the bearer token and echo the authenticated user."""
    return current_user
