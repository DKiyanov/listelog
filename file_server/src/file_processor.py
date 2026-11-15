from datetime import datetime
from pathlib import Path
import shutil

from src.audio_sender import *

class FileProcessor:
  def __init__(
      self,
      work_dir: Path,  # каталог для хранения временных файлов
      wait_result_file: Path,  # файл со списком файлов, ожидающих результата обработки
      sender: AudioSender,  # отправщик файлов на сервер для обработки
      file_lock: asyncio.Lock,
  ):
    self.work_dir = work_dir
    self.wait_result_file = wait_result_file
    self.sender = sender
    self.file_lock = file_lock

    # Гарантируем, что рабочая директория и папка для файла результатов существуют
    self.work_dir.mkdir(parents=True, exist_ok=True)
    self.wait_result_file.parent.mkdir(parents=True, exist_ok=True)

  def _clear_directory(self, dir_path: Path) -> None:
      for item in dir_path.iterdir():
          try:
              if item.is_file() or item.is_symlink():
                  item.unlink(missing_ok=True)
          except OSError:
              pass  # Игнорируем ошибки доступа, блокировки файлов и т.д.

  async def proc_file(
      self, file_path: Path, mono: bool, diarize: bool
  ) -> None:
    if not file_path.is_file():
      raise FileNotFoundError(f"Исходный файл не найден: {file_path}")

    self._clear_directory(self.work_dir)

    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
    temp_file_path = self.work_dir / f"{timestamp}{file_path.suffix}"

    try:
      shutil.copy2(file_path, temp_file_path)

      sid = await self.sender.file_upload(str(temp_file_path), file_path.stem, mono, diarize)

      # Добавление записи в wait_result_file в формате <sid>\t<file_path>
      async with self.file_lock:
        with self.wait_result_file.open("a", encoding="utf-8") as f:
            f.write(f"{sid}\t{file_path}\n")

    finally:
      self._clear_directory(self.work_dir)
