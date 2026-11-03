import asyncio
import json
import logging
import random
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from collections.abc import Awaitable, Callable

from src.config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("file-server")

# ---------------------------------------------------------------------------
# Константы статусов
# ---------------------------------------------------------------------------
STATUS_NEW = "N"
STATUS_PROCESSING = "P"
STATUS_RESULT = "R"
STATUS_ERROR = "E"

MEDIA_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus",
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg",
}

SUBDIRS = {
    STATUS_NEW: "new",
    STATUS_PROCESSING: "processing",
    STATUS_RESULT: "result",
    STATUS_ERROR: "error",
}


def ensure_dirs(config: Config) -> None:
    for sub in SUBDIRS.values():
        (config.data_dir / sub).mkdir(parents=True, exist_ok=True)
    config.info_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Хранилище состояния сканирования
#
# Для каждого источника храним:
#   files: {abs_path: {"status": "seen" | "uploaded", "upload_name": str|None}}
# Файл состояния: <data_dir>/.scan_state/<hash>.json
# ---------------------------------------------------------------------------
class ScanState:
    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict] = {}

    def _path_for(self, source: Path) -> Path:
        key = str(source)
        # безопасное имя файла
        safe = "".join(c if c.isalnum() else "_" for c in key)
        return self.state_dir / f"{safe}.json"

    def load(self, source: Path) -> dict:
        key = str(source)
        if key in self._cache:
            return self._cache[key]
        p = self._path_for(source)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}
        self._cache[key] = data
        return data

    def save(self, source: Path) -> None:
        key = str(source)
        p = self._path_for(source)
        p.write_text(json.dumps(self._cache.get(key, {}), ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------
def is_media_file(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_EXTENSIONS


def generate_unique_name(data_dir: Path) -> str:
    """10-значное случайное число, уникальное для каталогов data_dir."""
    existing = set()
    for sub in SUBDIRS.values():
        d = data_dir / sub
        if d.exists():
            for p in d.iterdir():
                if p.is_file():
                    existing.add(p.stem)
    for _ in range(1000):
        num = random.randint(1_000_000_000, 9_999_999_999)
        if str(num) not in existing:
            return str(num)
    raise RuntimeError("Не удалось сгенерировать уникальное имя файла")


def write_info(info_dir: Path, num: str, data: dict) -> None:
    p = info_dir / f"{num}_inf.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def read_info(info_dir: Path, num: str) -> Optional[dict]:
    p = info_dir / f"{num}_inf.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def update_info(info_dir: Path, num: str, **fields) -> None:
    data = read_info(info_dir, num) or {}
    data.update(fields)
    write_info(info_dir, num, data)


def move_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


# ---------------------------------------------------------------------------
# Очередь обработки (asyncio)
# ---------------------------------------------------------------------------
@dataclass
class QueueItem:
    num: str          # имя файла без расширения
    file_path: Path   # путь в data_dir/new/<num>.<ext>

ProcessFileHandler = Callable[[Path, str], Awaitable[str]]

# ---------------------------------------------------------------------------
# Сервер
# ---------------------------------------------------------------------------
class FileServer:
    def __init__(self, config: Config, folders: list[Path], pfh: ProcessFileHandler):
        self.config = config
        self.folders = folders
        self.pfh = pfh
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.scan_state = ScanState(config.data_dir / ".scan_state")
        self._stop = asyncio.Event()
        self._processing_lock = asyncio.Lock()

    # ---- инициализация ----
    async def startup(self) -> None:
        ensure_dirs(self.config)
        # очередь из new
        new_dir = self.config.data_dir / SUBDIRS[STATUS_NEW]
        count = 0
        for f in sorted(new_dir.iterdir()):
            if f.is_file() and is_media_file(f):
                await self.queue.put(QueueItem(num=f.stem, file_path=f))
                count += 1
        log.info("Очередь инициализирована: %d файлов", count)
        # восстановим недостающие info-файлы
        for f in new_dir.iterdir():
            if f.is_file() and is_media_file(f):
                if read_info(self.config.info_dir, f.stem) is None:
                    write_info(
                        self.config.info_dir,
                        f.stem,
                        {
                            "file_path": "",
                            "create_dt": datetime.now(timezone.utc).isoformat(),
                            "status": STATUS_NEW,
                            "error": "",
                        },
                    )

    # ---- периодическое сканирование ----
    async def scanner_loop(self) -> None:
        interval = max(self.config.scan_interval, 0.05) * 60.0
        while not self._stop.is_set():
            try:
                await self.scan_once()
            except Exception:
                log.exception("Ошибка при сканировании")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def scan_once(self) -> None:
        for source in self.folders:
            await self._scan_source(source)

    async def _scan_source(self, source: Path) -> None:
        if not source.is_dir():
            log.warning("Источник не найден: %s", source)
            return
        state = self.scan_state.load(source)
        # Собираем текущий список медиафайлов
        current: dict[str, Path] = {}
        for p in source.rglob("*"):
            try:
                if p.is_file() and is_media_file(p):
                    current[str(p.resolve())] = p
            except OSError:
                continue

        # 1) Файлы, которые были "seen" в прошлой итерации и снова появились -> загружаем
        to_upload: list[str] = []
        for path_str, meta in list(state.items()):
            if meta.get("status") == "seen":
                if path_str in current:
                    to_upload.append(path_str)
                else:
                    # исчез — забываем
                    state.pop(path_str, None)

        # 2) Уже загруженные — отслеживаем, чтобы не дублировать
        for path_str in current:
            if path_str not in state:
                state[path_str] = {"status": "seen"}
            elif state[path_str].get("status") == "uploaded":
                # файл уже был загружен — игнорируем
                pass

        # Загружаем
        for path_str in to_upload:
            src_path = Path(path_str)
            if not src_path.exists():
                state.pop(path_str, None)
                continue
            try:
                item = self._copy_to_new(src_path)
                state[path_str] = {"status": "uploaded", "num": item.num}
                log.info("Загружен файл %s -> %s", src_path, item.file_path)
                await self.queue.put(item)
            except Exception as e:
                log.exception("Не удалось загрузить %s: %s", src_path, e)

        self.scan_state.save(source)

    def _copy_to_new(self, src: Path) -> QueueItem:
        num = generate_unique_name(self.config.data_dir)
        ext = src.suffix
        dst = self.config.data_dir / SUBDIRS[STATUS_NEW] / f"{num}{ext}"

        # копируем с флагом "не перезаписывать"
        with src.open("rb") as fin, dst.open("xb") as fout:
            shutil.copyfileobj(fin, fout)

        # info
        write_info(
            self.config.info_dir,
            num,
            {
                "file_path": str(src.resolve()),
                "create_dt": datetime.now(timezone.utc).isoformat(),
                "status": STATUS_NEW,
                "error": "",
            },
        )
        return QueueItem(num=num, file_path=dst)

    # ---- обработчик очереди ----
    async def worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                await self._process_item(item)
            except Exception:
                log.exception("Ошибка обработки %s", item.num)
            finally:
                self.queue.task_done()

    async def _process_item(self, item: QueueItem) -> None:
        # переносим в processing + статус P
        # (это делаем до вызова start_file_processing, чтобы гарантировать
        #  атомарность перехода N -> P)
        try:
            file_info = read_info(self.config.info_dir, item.num) or {}
            dst = self.config.data_dir / SUBDIRS[STATUS_PROCESSING] / item.file_path.name
            move_file(item.file_path, dst)
            update_info(self.config.info_dir, item.num, status=STATUS_PROCESSING)
        except Exception as e:
            log.exception("Не удалось перевести в processing %s", item.num)
            try:
                update_info(self.config.info_dir, item.num, status=STATUS_ERROR, error=f"move to processing failed: {e}")
                err_dst = self.config.data_dir / SUBDIRS[STATUS_ERROR] / item.file_path.name
                if item.file_path.exists():
                    move_file(item.file_path, err_dst)
            except Exception:
                log.exception("Не удалось зафиксировать ошибку для %s", item.num)
            return

        # запускаем обработку — без параллелизма
        async with self._processing_lock:
            try:
                await self.start_file_processing(item.num, file_info["file_path"] or "")
            except Exception as e:
                log.exception("start_file_processing упал для %s", item.num)
                update_info(self.config.info_dir, item.num, status=STATUS_ERROR, error=str(e))
                try:
                    src = self.config.data_dir / SUBDIRS[STATUS_PROCESSING] / item.file_path.name
                    if src.exists():
                        move_file(src, self.config.data_dir / SUBDIRS[STATUS_ERROR] / item.file_path.name)
                except Exception:
                    log.exception("Не удалось переместить в error %s", item.num)

    # ---- управление ----
    async def run(self) -> None:
        await self.startup()
        tasks = [
            asyncio.create_task(self.scanner_loop(), name="scanner"),
            asyncio.create_task(self.worker_loop(), name="worker"),
        ]
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self) -> None:
        self._stop.set()

    async def start_file_processing(self, num: str, original_path:str) -> None:
        """
        - делает обработку файла (async, не блокирует поток)
        - обновляет местоположение файла и <num>_inf.json
        """
        log.info("start_file_processing(%s) — заглушка", num)
        # пример: найдём файл в processing
        processing_dir = self.config.data_dir / SUBDIRS[STATUS_PROCESSING]
        src = next((p for p in processing_dir.iterdir() if p.stem == num), None)
        if src is None:
            raise FileNotFoundError(f"processing file for {num} not found")

        error = await self.pfh(src, original_path)

        if error != "":
            raise RuntimeError(error)

        update_info(self.config.info_dir, num, status=STATUS_RESULT, error="")
