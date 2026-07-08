from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer

reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/login/access-token")


async def get_current_user(token: Annotated[str, Depends(reusable_oauth2)]):
    """Stub — Wave B1 fills this."""
    raise NotImplementedError("Wave B1 fills this")


CurrentUser = Annotated[object, Depends(get_current_user)]


async def get_current_active_superuser(current_user: CurrentUser):
    """Stub — Wave B1 fills this."""
    raise NotImplementedError("Wave B1 fills this")
