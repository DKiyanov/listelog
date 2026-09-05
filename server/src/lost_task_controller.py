import asyncio
from datetime import datetime
from pathlib import Path

from src.models import Task, get_task_key
from src.workers_manager import WorkersManager
from src.dispatcher import TaskDispatcher

class LostTasksController:
    def __init__(self, work_dir: str, workers_manager: WorkersManager, dispetcher: TaskDispatcher):
        self.work_dir = Path(work_dir)
        self.in_proc_path = self.work_dir / "in_proc"
        self.workers_manager = workers_manager
        self.dispetcher = dispetcher
        self._is_running = False
        self._task = None
        self.file_ext = "ogg"

    async def start(self):
        """Запускает фоновую задачу контроля потерянных воркеров."""
        if not self._is_running:
            self._is_running = True
            self._task = asyncio.create_task(self._loop())

    def stop(self):
        """Останавливает работу контроллера."""
        self._is_running = False
        if self._task:
            self._task.cancel()

    async def _loop(self):
        """Цикл, запускающий проверку раз в минуту."""
        while self._is_running:
            try:
                await self.check_lost_tasks()
            except Exception as e:
                # Здесь можно добавить логирование ошибок
                print(f"Ошибка при проверке задач: {e}")
            
            await asyncio.sleep(60)

    async def check_lost_tasks(self):
        """Сканирует директорию и проверяет зависшие файлы."""
        if not self.in_proc_path.exists():
            return

        now = datetime.now()
        
        # Получаем список id задач, которые сейчас реально обрабатываются активными воркерами
        active_task_keys = {
            w.task_key for w in self.workers_manager.active_workers 
            if w.task_key is not None
        }

        # Ищем все файлы .ogg в директории
        for file_path in self.in_proc_path.glob(f"*.{self.file_ext}"):
            # Извлекаем имя без расширения (например, "task123_2608261430")
            file_name = file_path.stem
            
            # Разделяем по последнему подчеркиванию, чтобы правильно обработать task_key со знаками _
            if "_" not in file_name:
                continue
                
            sid, cid, work_type, next_work_type, last_time_str = file_name.rsplit("_", 4)
            task_key = get_task_key(sid, cid, work_type)

            try:
                # Парсим формат ГГММДДччмм (например, 2608261430 -> 2026-08-26 14:30)
                last_time = datetime.strptime(last_time_str, "%y%m%d%H%M")
            except ValueError:
                # Пропускаем файлы с некорректным форматом даты
                continue

            if last_time <= now and task_key not in active_task_keys:
                await self.return_task(sid, cid, work_type, next_work_type, file_path)

    async def return_task(self, sid: str, cid: str, work_type: str, next_work_type:str, file: Path):
        """Возвращает задачу в очередь"""
        target_file = self.work_dir / work_type / f"{sid}_{cid}.{self.file_ext}"
        file.rename(target_file)

        await self.dispetcher.add_task(Task(
            work_type=work_type,
            sid=sid,
            cid=cid,
            file=target_file.name,
            next=next_work_type
        ))
