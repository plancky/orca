from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google")
async def google_oauth_stub():
    raise HTTPException(
        status_code=501, detail="Google OAuth not implemented in Phase 1"
    )
