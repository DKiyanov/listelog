import asyncio
import logging
from pathlib import Path
from typing import Protocol

from src.net.net_client import *
from src.result_prepare import *

logger = logging.getLogger(__name__)

class ResultWaiter:
    def __init__(
        self,
        wait_result_file: Path,
        net_client: NetClient,
        interval: int,
        file_lock: asyncio.Lock,
    ) -> None:
        self.wait_result_file = wait_result_file
        self.net_client = net_client
        self.interval = interval
        self.file_lock = file_lock
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Запускает бесконечный цикл опроса до вызова stop()."""
        logger.info("Запуск ResultWaiter для файла %s", self.wait_result_file)
        while not self._stop_event.is_set():
            try:
                await self._process_cycle()
            except Exception:
                logger.exception("Непредвиденная ошибка в цикле опроса")

            # Ожидание интервала с возможностью мгновенного прерывания при stop()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

        logger.info("ResultWaiter остановлен")

    def stop(self) -> None:
        """Сигнализирует воркеру о необходимости завершить работу."""
        self._stop_event.set()

    async def _get_sid_result(self, sid) -> tuple[str, int]:
        session_status = await self.net_client.get_session_status(sid)
        if not session_status.is_closed or session_status.percent < 100:
            return "", session_status.percent

        stts_results, stts_spks = await self.net_client.get_session_data(sid)

        spkimap: dict[str, Speaker] = {}
        spknmap: dict[str, str] = {}
        prepare_speakers(stts_results, stts_spks, spkimap, spknmap)

        text =  result_get_text(TextFormat.subtitle, stts_results, spknmap)
        return text, 100

    async def _process_cycle(self) -> None:
        records = await self._read_records()
        if not records:
            return

        completed_sids: set[str] = set()

        for sid, file_path in records:
            if self._stop_event.is_set():
                break

            try:
                result, percent = await self._get_sid_result(sid)
            except Exception as e:
                logger.error("Ошибка при запросе статуса для sid=%s: %s", sid, e)
                continue

            if percent == 100:
                srt_path = Path(file_path).with_suffix(".srt")
                try:
                    await asyncio.to_thread(self._save_result, srt_path, result)
                    completed_sids.add(sid)
                    logger.info("Результат для sid=%s сохранен в %s", sid, srt_path)
                except Exception as e:
                    logger.error("Ошибка сохранения файла %s: %s", srt_path, e)

        if completed_sids:
            await self._remove_completed(completed_sids)

    async def _read_records(self) -> list[tuple[str, str]]:
        """Безопасно читает текущие записи из файла."""
        async with self.file_lock:
            return await asyncio.to_thread(self._read_file_sync)

    async def _remove_completed(self, completed_sids: set[str]) -> None:
        """Атомарно удаляет обработанные sid, сохраняя новые записи от других воркеров."""
        async with self.file_lock:
            await asyncio.to_thread(self._filter_and_write_sync, completed_sids)

    def _read_file_sync(self) -> list[tuple[str, str]]:
        if not self.wait_result_file.exists():
            return []

        records: list[tuple[str, str]] = []
        content = self.wait_result_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", maxsplit=1)
            if len(parts) == 2:
                records.append((parts[0], parts[1]))
            else:
                logger.warning("Некорректный формат строки в файле: %r", line)
        return records

    def _filter_and_write_sync(self, completed_sids: set[str]) -> None:
        if not self.wait_result_file.exists():
            return

        # Перечитываем файл под блокировкой, чтобы не затереть свежедобавленные строки
        current_records = self._read_file_sync()
        remaining_lines = [
            f"{sid}\t{path}\n"
            for sid, path in current_records
            if sid not in completed_sids
        ]

        # Пишем во временный файл и делаем атомарную замену
        temp_file = self.wait_result_file.with_suffix(".tmp")
        temp_file.write_text("".join(remaining_lines), encoding="utf-8")
        temp_file.replace(self.wait_result_file)

    @staticmethod
    def _save_result(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
