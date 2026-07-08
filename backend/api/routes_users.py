"""User management endpoints.

``/signup`` is public and binds ``UserRegister`` (email/password/full_name only),
then constructs ``UserCreate`` from those three fields explicitly — so
``is_superuser`` / ``is_active`` can never be set from the request body
(privilege-escalation guard). ``/`` is superuser-gated admin provisioning.
"""

from fastapi import APIRouter, Depends, HTTPException

from backend import crud
from backend.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from backend.db.models import User, UserCreate, UserPublic, UserRegister

router = APIRouter(prefix="/users", tags=["users"])


@router.post(
    "/",
    response_model=UserPublic,
    status_code=201,
    dependencies=[Depends(get_current_active_superuser)],
)
async def create_user(session: SessionDep, user_in: UserCreate) -> User:
    existing = await crud.get_user_by_email(session, user_in.email)
    if existing is not None:
        raise HTTPException(
            status_code=400, detail="A user with this email already exists"
        )
    return await crud.create_user(session, user_in)


@router.get("/me", response_model=UserPublic)
async def read_user_me(current_user: CurrentUser) -> User:
    return current_user


@router.post("/signup", response_model=UserPublic, status_code=201)
async def signup(session: SessionDep, user_in: UserRegister) -> User:
    existing = await crud.get_user_by_email(session, user_in.email)
    if existing is not None:
        raise HTTPException(
            status_code=400, detail="A user with this email already exists"
        )
    # Explicit field copy — is_superuser/is_active take safe defaults and cannot
    # be injected from the public request body.
    user_create = UserCreate(
        email=user_in.email,
        password=user_in.password,
        full_name=user_in.full_name,
    )
    return await crud.create_user(session, user_create)
