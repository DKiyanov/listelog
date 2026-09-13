import asyncio
from typing import List
from dataclasses import asdict
from fastapi import WebSocket
from pathlib import Path

from src.config import *
from src.models import Worker, Task, get_task_key

class TaskDispatcher:

    def __init__(self, config: Config):
        self.config = config
        self.tasks_dir:str = config.audio_data_dir
        self.task_queue: List[Task] = []
        self.workers: List[Worker] = []
        self.ready_workers: List[Worker] = []
        self._lock = asyncio.Lock()

    async def add_task(self, task: Task):
        """Добавляет задачу под локом и запускает диспетчеризацию."""
        async with self._lock:
            self.task_queue.append(task)
            print(f"задача добавлена в очередь {task.work_type} {task.file}")
            self._dispatch_tasks_inside_lock()

    async def remove_task(self, sid: str, cid: str, work_type:str):
        async with self._lock:
            index = next((i for i, task in enumerate(self.task_queue) 
                if (task.sid == sid 
                and task.cid == cid 
                and task.work_type == work_type)
            ), -1)

            if index >= 0:
                del self.task_queue[index]

    async def on_worker_ready(self, worker: Worker):
        """Добавляет воркера под локом и запускает диспетчеризацию."""
        async with self._lock:
            if not any(w.ws == worker.ws for w in self.workers):
                self.ready_workers.append(worker) 
            if not any(w.ws == worker.ws for w in self.ready_workers):
                self.ready_workers.append(worker)
                print(f"воркер готов к приёму задачи {worker.work_types}")
            self._dispatch_tasks_inside_lock()

    async def on_worker_unready(self, ws: WebSocket):
        """Удаляет воркера из списка готовых, если его WebSocket отключился."""
        async with self._lock:
            # Ищем воркера с совпадающим вебсокетом и удаляем его
            index = next((i for i, w in enumerate(self.workers) if w.ws == ws), -1)
            if index >= 0:
                del self.workers[index]

            index = next((i for i, w in enumerate(self.ready_workers) if w.ws == ws), -1)
            if index >= 0:
                worker = self.ready_workers[index]
                print(f"воркер отмена готовности {worker.work_types}")
                del self.ready_workers[index]
            

    def _dispatch_tasks_inside_lock(self):
        """Внутренний метод распределения задач.

        Должен вызываться ТОЛЬКО внутри блока async with self._lock.
        """
        for task in list(self.task_queue):
            work_type = task.work_type

            suitable_worker = next((w for w in self.ready_workers if work_type in w.work_types), None)

            if not suitable_worker and work_type == "stt":
                # Допустима отправка stt в sttd воркер
                # вообще если нет зарегистрированных обработчиков для stt
                if not any(worker for worker in self.workers if work_type in worker.work_types):
                    work_type = "sttd"
                    suitable_worker = next((w for w in self.ready_workers if work_type in w.work_types), None)
                    if suitable_worker:
                        self._move_stt_to_sttd(task)

            if suitable_worker:
                # Воркер и задача забираются из списков ДО сетевого await
                self.ready_workers.remove(suitable_worker)
                self.task_queue.remove(task)

                print(f"Планирование отправки задания воркеру {work_type} {task.file}")

                # Запускаем отправку в фоновом режиме, не удерживая лок
                asyncio.create_task(
                    self._send_task_to_worker(suitable_worker, task, work_type)
                )

    def _move_stt_to_sttd(self, task: Task) -> None:
        stt_file_path = Path(self.config.audio_data_dir) / "stt" / task.file
        sttd_file_path = Path(self.config.audio_data_dir) / "sttd" / task.file
        stt_file_path.rename(sttd_file_path)
        
    async def _send_task_to_worker(self, worker: Worker, task: Task, work_type: str):
        """Фоновое асинхронное выполнение отправки за пределами общего лока."""
        try:
            print(f"Отправка задания воркеру {work_type} {task.file}")
            task_dict = asdict(task)
            task_dict["type"] = "task"
            task_dict["work_type"] = work_type
            worker.task_key = get_task_key(task.sid, task.cid, work_type)
            await worker.ws.send_json(task_dict)
        except Exception as e:
            print(f"Ошибка отправки задачи {task.work_type} {task.file} воркеру: {e}")

            # При ошибке мы ОБЯЗАНЫ снова взять лок, чтобы безопасно изменить списки
            async with self._lock:
                # Возвращаем задачу в начало очереди
                self.task_queue.insert(0, task)
                print(f"Задание вернули в очередь {task.work_type} {task.file}")

                # Пытаемся перераспределить вернувшуюся задачу среди других воркеров
                self._dispatch_tasks_inside_lock()

    async def _fill_initial_tasks(self, tasks_dir: str):
        tasks_path = Path(tasks_dir)
        
        # Проверяем, существует ли базовая директория
        if not tasks_path.is_dir():
            return

        # Перебираем только подкаталоги первого уровня
        for sub_dir in tasks_path.iterdir():
            if sub_dir.is_dir():
                work_type = sub_dir.name.lower()

                if work_type in ["in_proc", "complete", "error"]:
                    continue
                
                # Ищем файлы *.ogg внутри текущего подкаталога и сортируем по дате создания
                sorted_files = sorted(sub_dir.glob("*.ogg"), key=lambda f: f.stat().st_ctime)

                for file_path in sorted_files:
                    sid, cid = file_path.stem.split("_")
                    # Создаем задачу, используя имя подкаталога как work_type
                    await self.add_task(Task(
                        work_type=work_type, 
                        sid=sid,
                        cid=cid,
                        file=file_path.name,
                        next=""
                    ))

    def task_completion(self, sid: str, cid: str, file: str):
        complete_file_path = Path(self.config.audio_data_dir) / "complete" / file
        session_proc_dir = Path(self.config.sessions_dir) / sid / "proc"
        session_proc_dir.mkdir(exist_ok=True)
        complete_file_path.rename(session_proc_dir / file)        

    async def start(self)->None:
        await self._fill_initial_tasks(self.tasks_dir) 
