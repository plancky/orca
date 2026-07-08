from fastapi import APIRouter, WebSocket

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/query")
async def ws_query_stub(websocket: WebSocket):
    """Stub — Wave E3 fills this."""
    await websocket.accept()
    await websocket.send_text(
        '{"type":"error","detail":"WebSocket not yet implemented"}'
    )
    await websocket.close()
