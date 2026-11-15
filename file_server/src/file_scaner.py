import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

SCAN_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg", ".opus",
    ".aac", ".m4a", ".wma", ".aiff", ".alac",
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", 
    ".webm", ".flv", ".m4v", ".mpg", ".mpeg", 
    ".3gp", ".ts", ".vob", ".ogv"
}

# Настраиваем базовое логирование, чтобы видеть ошибки, если они возникнут
logger = logging.getLogger(__name__)

# Асинхронный коллбэк принимает Path и возвращает Awaitable (корутину)
OnProcFile = Callable[[Path], Awaitable[None]]


class FileScanner:
    def __init__(
        self,
        scan_catalogs: list[str],
        procd_file: Path,
        interval: int,
        on_proc_file: OnProcFile,
    ):
        self.scan_catalogs = [Path(p) for p in scan_catalogs]
        self.procd_file = Path(procd_file)
        self.interval = interval
        self.on_proc_file = on_proc_file

        # Множество (set) для быстрого поиска O(1) уже обработанных путей
        self._processed: set[str] = set()
        # Очередь файлов, собранная на этапе 2 для обработки на этапе 1
        self._pending_files: list[Path] = []
        self._is_running: bool = False

    def _load_processed(self) -> None:
        """Загружает список уже обработанных файлов при старте."""
        if not self.procd_file.exists():
            # Создаем родительские директории и пустой файл, если его еще нет
            self.procd_file.parent.mkdir(parents=True, exist_ok=True)
            self.procd_file.touch()
            return

        with self.procd_file.open("r", encoding="utf-8") as f:
            # Убираем переносы строк и пустые строки
            self._processed = {line.strip() for line in f if line.strip()}

    def _mark_as_processed(self, file_path: Path) -> None:
        """Добавляет файл в память и дописывает в procd_file."""
        path_str = str(file_path)
        self._processed.add(path_str)
        with self.procd_file.open("a", encoding="utf-8") as f:
            f.write(f"{path_str}\n")

    def _scan_directories_sync(self) -> list[Path]:
        """Синхронный обход каталогов и подкаталогов (вызывается в отдельном потоке)."""
        new_files: list[Path] = []

        for catalog in self.scan_catalogs:
            if not catalog.exists() or not catalog.is_dir():
                logger.warning(f"Каталог недоступен или не существует: {catalog}")
                continue

            try:
                for item in catalog.rglob("*"):
                    if item.is_file() and item.suffix.lower() in SCAN_EXTENSIONS:
                        path_str = str(item)
                        if path_str not in self._processed:
                            new_files.append(item)
            except OSError as e:
                logger.error(f"Ошибка доступа при сканировании {catalog}: {e}")

        return new_files

    async def _step_1_process_pending(self) -> None:
        """Этап 1: Последовательная обработка файлов из предыдущего цикла."""
        if not self._pending_files:
            return

        logger.info(f"Начинаем обработку {len(self._pending_files)} файлов...")
        for file_path in self._pending_files:
            if not self._is_running:
                break

            try:
                await self.on_proc_file(file_path)
            except Exception as e:
                # Если произошла ошибка, не добавляем в procd_file — попробуем в следующий раз
                logger.error(f"Ошибка при обработке файла {file_path}: {e}", exc_info=True)
            else:
                # Успешное выполнение — фиксируем результат
                await asyncio.to_thread(self._mark_as_processed, file_path)
                logger.info(f"Файл успешно обработан: {file_path}")

        # Очищаем очередь после прохода
        self._pending_files.clear()

    async def _step_2_scan_new(self) -> None:
        """Этап 2: Поиск новых файлов для следующего цикла."""
        # Выполняем блокирующий ввод-вывод в отдельном потоке
        self._pending_files = await asyncio.to_thread(self._scan_directories_sync)
        if self._pending_files:
            logger.info(f"Найдено новых файлов для следующего цикла: {len(self._pending_files)}")

    async def start(self) -> None:
        """Основной бесконечный цикл работы воркера."""
        self._is_running = True
        await asyncio.to_thread(self._load_processed)
        logger.info(f"Воркер запущен. Загружено записей из истории: {len(self._processed)}")

        while self._is_running:
            # 1. Обработка файлов из предыдущего цикла (на 1-й итерации список пуст)
            await self._step_1_process_pending()

            # 2. Сканирование каталогов для формирования списка на следующий цикл
            await self._step_2_scan_new()

            # Ждем заданный интервал перед следующим циклом
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        """Мягкая остановка воркера."""
        self._is_running = False
