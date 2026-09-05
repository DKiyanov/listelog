from src.config import Config
from fastapi import WebSocket
from pathlib import Path
import json

from src.config import *

class ClientManager:
    def __init__(self, config: Config):
        self.clients: dict[str, WebSocket] = {}  # sid -> ws
        self.sessions_dir = Path(config.sessions_dir)

    async def connect_client(self, sid: str, ws: WebSocket):
        await ws.accept()
        self.clients[sid] = ws

    def disconnect_client(self, sid: str):
        self.clients.pop(sid, None)

    async def send_result(self, sid: str, cid: str):
        """Отправляет результат клиенту, если он подключен."""
        print("send result to client")
        ws = self.clients.get(sid)
        if not ws:
            return
        result_path = self.sessions_dir / sid / "result" / f"{cid}_result.json"
        if not result_path.exists():
            return
        result_data = json.loads(result_path.read_text(encoding="utf-8"))
        await ws.send_json({
            "type": "result",
            "sid": sid,
            "cid": int(cid),
            "data": result_data
        })