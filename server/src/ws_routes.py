from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.init import client_manager, workers_manager

router = APIRouter()

@router.websocket("/ws/worker")
async def ws_worker(websocket: WebSocket):
    await workers_manager.connect_worker(websocket)

@router.websocket("/ws/client/{sid}")
async def ws_client(sid: str, websocket: WebSocket):
    await client_manager.connect_client(sid, websocket)
    try:
        # Держим соединение открытым, слушаем сообщения (если нужно)
        while True:
            await websocket.receive_text()  # или ping
    except WebSocketDisconnect:
        client_manager.disconnect_client(sid)