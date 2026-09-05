from fastapi import WebSocket, WebSocketDisconnect
from src.dispatcher import TaskDispatcher
from src.client_manager import ClientManager

from src.models import Worker, Task

class WorkersManager:
    def __init__(self, dispatcher: TaskDispatcher, client_manager: ClientManager):
        self.dispatcher = dispatcher
        self.client_manager = client_manager
        self.active_workers: list[Worker] = []

    async def connect_worker(self, ws: WebSocket):
        await ws.accept()

        worker = next((worker for worker in self.active_workers if worker.ws == ws), None)
        if worker is None:
            worker = Worker(ws)
            self.active_workers.append(worker)

        try:
            while True:
                data = await ws.receive_json()
                task_type: str = data.get("type")

                ack_id = data.get("ack_id")
                if ack_id and task_type != "ack":
                    await ws.send_json({"type": "ack", "ack_id": ack_id})

                if task_type == "ready":
                    worker.work_types = str(data.get("work_types")).lower().replace(" ", "").split(",")
                    await self.dispatcher.on_worker_ready(worker)
                    continue

                if task_type == "task_done":
                    work_type = str(data.get("work_type"))
                    sid  = str(data.get("sid"))
                    cid  = str(data.get("cid"))
                    file = str(data.get("file"))
                    next_work_type = str(data.get("next")).lower()

                    await self.dispatcher.remove_task(sid, cid, work_type) # задание могло вернуться в очередь - удаляем его

                    if next_work_type == "":
                        next_work_type = "complete"

                    if next_work_type != "complete":
                        await self.dispatcher.add_task(Task(next_work_type, sid, cid, file, ""))
                    else:
                        await self._task_completion(sid, cid, file)
                    continue

        except WebSocketDisconnect:
            await self.dispatcher.on_worker_unready(ws)
            self.active_workers.remove(worker)

    async def _task_completion(self, sid: str, cid: str, file: str):
        self.dispatcher.task_completion(sid, cid, file)
        await self.client_manager.send_result(sid, cid)
