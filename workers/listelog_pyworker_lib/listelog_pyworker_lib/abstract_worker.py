import asyncio
from typing import Optional
import websockets
import json
from pathlib import Path
import time
import logging
from datetime import datetime

from .file_processor import *
from .models import *
from .config import *

_logger = logging.getLogger()

class Worker:
    def __init__(self, file_processor_class: type[FileProcessor]):
        config_path = get_worker_config_path()
        self._config = WorkerConig(config_path)
        main_config = MainConfig(config_path)

        log_level = logging.INFO if self._config.verbose else logging.WARNING
        logging.basicConfig(level=log_level)

        self.main_url = self._config.main_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws/worker"

        if config_path == "env":
            class_config = "env"
        else:
            class_config = get_class_config_path(file_processor_class)        

        self._file_processor = file_processor_class(main_config, class_config)

        self._audio_dir = Path(self._config.audio_data_dir)

        self._in_proc_dir = self._audio_dir / "in_proc"
        self._in_proc_dir.mkdir(exist_ok=True)

        self.task = Task(self._in_proc_dir)

        self._running: bool = False

        self.ws = None

        self.poll_interval_sec: float = 1.0

        self.pending_task_done: str | None = None
        self.pending_ack_id: str | None = None

    async def _connect_with_retry(self):
        """При старте пытается подключиться к MAIN каждые 10 секунд в течение config.wait_time минут."""
        deadline = asyncio.get_event_loop().time() + self._config.wait_time * 60
        while True:
            try:
                self.ws = await websockets.connect(self.main_url)
                _logger.info(f"connected to main server")
                return
            except Exception:
                if asyncio.get_event_loop().time() > deadline:
                    raise RuntimeError(f"Cannot connect to MAIN within {self._config.wait_time} minutes")
                await asyncio.sleep(10)

    async def _send_ready(self):
        if self.ws is None: return    
        work_types = self._file_processor.get_work_types()    
        await self.ws.send(json.dumps({"type": "ready", "work_types": work_types}))
        _logger.info(f"Сервер MAIN уведомлён о готовности обработчика принимать задания")

    async def _process_task(self, task: dict):
        if self.ws is None: return
        work_type = str(task["work_type"])
        sid = str(task["sid"])
        cid = str(task["cid"])
        file_name = str(task["file"])

        next_sub_dir: str = ""

        task_next = task.get("next")
        if task_next is not None:
            next_sub_dir = task_next
        if next_sub_dir == "" and self._config.next_work_type:
            next_sub_dir = self._config.next_work_type

        target_file = self._audio_dir / work_type / file_name

        next = await self._process_file(work_type, sid, cid, target_file, next_sub_dir)

        # 4. Уведомляем MAIN о завершении
        self.pending_task_done = json.dumps({
            "type": "task_done",
            "work_type": work_type,
            "sid": sid,
            "cid": cid,
            "file": file_name,
            "next": next,
            "ack_id": file_name,
        })
        self.pending_ack_id = file_name

        await self.ws.send(self.pending_task_done)

        _logger.info(f"Задание выполнено, серверу MAIN результат отправлен")        

    async def _process_file(self, work_type: str, sid: str, cid: str, target_file: Path, next_work_type: str) -> str:
        _logger.info(f"{datetime.now().strftime('%H:%M:%S')}, Получено задание на обработку файла: {work_type}/{target_file.name}")

        if not target_file.exists():
            _logger.info(f"файла: {work_type}/{target_file.name} - отсутствует")
            return "error"

        original_name = target_file.name

        self.task.work_type = work_type
        self.task.sid = sid
        self.task.cid = cid
        self.task.original_file = str(target_file.resolve())
        self.task.next_work_type = next_work_type
        self.task.session_dir = Path(self._config.sessions_dir) / sid
        self.task.in_proc_file = None
        self.task.last_time = None

        try:
            next = await self._file_processor.process_file(self.task)
        except Exception as e:
            _logger.error(f"Ошибка при обработке фйла {work_type}/{target_file.name}: {e}", exc_info=True)
            next = "error"

        assert self.task.in_proc_file is not None

        if next_work_type != "":
            next = next_work_type

        if next == "":
            next = "complete"

        next_path = self._audio_dir / next
        next_path.mkdir(exist_ok=True)

        next_file = next_path / original_name
        self.task.in_proc_file.rename(next_file)

        _logger.info(f"{datetime.now().strftime('%H:%M:%S')}, Обработка файла: {work_type}/{target_file.name}, завершена, next = { next }")

        return next    

    def _get_oldest_file(self, files_dir: Path) -> Optional[Path]:
        """Находит файл с самым ранним временем создания в директории."""
        try:
            # Итерируемся только по файлам (исключаем поддиректории)
            files: list[Path] = [f for f in files_dir.iterdir() if f.is_file()]
            if not files:
                return None

            # Сортируем по времени изменения/создания (stat().st_mtime надежнее на POSIX)
            return min(files, key=lambda f: f.stat().st_mtime)

        except Exception as e:
            _logger.error(f"Ошибка при сканировании директории {files_dir}: {e}", exc_info=True)
            return None

    async def _fs_run(self) -> None:
        work_types = self._file_processor.get_work_types().lower().replace(" ", "").split(",")   

        for work_type in work_types:
            asyncio.create_task(self._fs_run_work_type(work_type))   

    async def _fs_run_work_type(self, work_type: str) -> None:
        files_dir = self._audio_dir / work_type
        files_dir.mkdir(exist_ok=True) 

        _logger.info(f"Обработка каталога {files_dir} запущена")
        self._running = True

        next_sub_dir: str = ""
        if self._config.next_work_type:
            next_sub_dir = self._config.next_work_type        

        while self._running:
            try:
                target_file = self._get_oldest_file(files_dir)

                if target_file is None:                    
                    time.sleep(self.poll_interval_sec) # Директория пуста, ждем новые файлы
                    continue   

                sid, cid = target_file.stem.split("_")                             
                
                await self._process_file(work_type, sid, cid, target_file, next_sub_dir)

            except KeyboardInterrupt:
                _logger.info("Получен сигнал остановки от пользователя.")
                self.stop()
            except Exception as e:
                _logger.error(f"Непредвиденная ошибка в цикле воркера: {e}", exc_info=True)    

    def _move_next(self, target_file: Path, next: str) -> None:
        file_name = target_file.stem # имя файла без расширения 
        sid, cid = file_name.split("_")  

        if next != "":
            next_path = self._audio_dir / next
        else:
            next_path = Path(self._config.sessions_dir) / sid / "proc"

        next_path.mkdir(exist_ok=True)
        next_file_path = next_path / target_file.name
        target_file.rename(next_file_path)                    

    async def _ws_run(self):
        self._running = True
        while self._running:
            await self._connect_with_retry()
            if self.ws is None: return  

            try:
                if self.pending_task_done is not None:
                    await self.ws.send(self.pending_task_done)
                else:
                    await self._send_ready()

                async for msg in self.ws:
                    data = json.loads(msg)
                    msg_type = str(data.get("type"))

                    if msg_type == "ack":
                        ack_id = str(data.get("ack_id"))  
                        if self.pending_ack_id is not None and self.pending_ack_id == ack_id:                      
                            self.pending_task_done = None
                            self.pending_ack_id = None
                            await self._send_ready()   # снова готов 

                    if msg_type == "task":
                        await self._process_task(data)

            except websockets.ConnectionClosed as e:
                _logger.error("Соединение закрыто")
            except KeyboardInterrupt:
                _logger.info("Получен сигнал остановки от пользователя.")
                self.stop()
            except Exception as e:
                _logger.error(f"Непредвиденная ошибка в цикле воркера: {e}", exc_info=True)

    async def run(self) -> None:
        if self._config.mode == "fs":
            await self._fs_run()
        else:
            await self._ws_run()        

    def stop(self) -> None:
        """останавливает воркер."""
        _logger.info(f"Остановка {self._file_processor.get_work_types()}...")
        self._running = False
             