import websockets
import asyncio
import json
from typing import List, Dict, Any

from src.models import *
from src.net.net_client import NetClient

class ResultReceiver:
    def __init__(self, base_url: str, sid: str, net_client: NetClient):
        self._ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._sid = sid
        self._ws = None
        self._listen_task: asyncio.Task | None = None
        self._net_client = net_client
        self._is_working: bool = False

    async def start(self) -> None:
        self._listen_task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        self._is_working = False

        if self._ws:
            await self._ws.close()
            self._ws = None

        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass        
            self._listen_task = None

    async def _listen(self):
        attempt = 0
        max_delay = 30  # Максимальная пауза между попытками в секундах  

        self._is_working = True
        while self._is_working:
            try:
                self._ws = await websockets.connect(f"{self._ws_url}/ws/client/{self._sid}")
                print("ResultReceiver connected to server")
                if attempt > 0:
                    print("не первая попытка, значит что то могли пропустить")
                    await self._net_client.load_results()

                attempt = 0

                async for msg in self._ws:
                    data: Dict[str, Any] = json.loads(msg)
                    if data["type"] == "result" and data["sid"] == self._sid:
                        print("result processing")
                        self._net_client.add_result(data)
                        
            except  (websockets.ConnectionClosed, OSError) as e:
                self._ws = None
                attempt += 1
                # Считаем задержку: 2^attempt + случайный шум
                delay = min(max_delay, (2 ** attempt))
                
                print(f"Соединение разорвано. Ошибка: {e}")
                print(f"Следующая попытка переподключения через {delay:.2f} сек...")
                
                await asyncio.sleep(delay)

        self._ws = None
        self._listen_task = None

