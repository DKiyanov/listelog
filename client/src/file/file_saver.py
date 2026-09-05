from __future__ import annotations
import asyncio
from typing import Optional, Callable, List, Tuple
import numpy as np
import soundfile as sf
import time

from src.models import *

class FileSaver:
    def __init__(self) -> None:
        self._raw_queue: asyncio.Queue[Optional[RawChunk]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._loop = asyncio.AbstractEventLoop = asyncio.get_running_loop()
        self._is_running: bool = False
        self._is_recording = False

    async def start_recording(self, file_path: str, source_list: set[int], sample_rate) -> None:
        self._file_path = file_path
        self._source_list = source_list
        self._num_channels = len(source_list)
        self._source_to_idx = {src_id: idx for idx, src_id in enumerate(source_list)}
        self._source_pos = {src_id : 0 for src_id in source_list} 

        self._sample_rate: int = sample_rate

        self.file_handle = sf.SoundFile(
            file_path, 
            mode='w', 
            samplerate=self._sample_rate, 
            channels=self._num_channels, 
            format='OGG', 
            subtype='OPUS'
        )        

        self._buf_target_level = (self._sample_rate // 10) * 1 # 100мс
        self._buf_triger_level = self._buf_target_level * 2 # 200мс
        self._buffer_size = self._buf_target_level * 3 # 300мс 
        self._buffer = np.zeros((self._buffer_size, self._num_channels), dtype=np.float32)

        # Рассуждаем об этом так
        # Наполняется ванна self.buffer_size - она сделана с запасом, чтоб не потекло через верх
        # если вода доходит уровня срабатывания self.buf_triger_level, спускаем её до целевого уровня self.buf_target_level
        # тригер self.buf_triger_level срабатывает при достижении его в любом из каналов
        
        self._file_written_samples = 0 # Сколько сэмплов ОКОНЧАТЕЛЬНО сброшено в файл
        self._start_time = time.monotonic()
        self._is_recording = True

        await self._start_processing()

    async def add_chunk(self, chunk: RawChunk) -> None:
        if not self._is_recording or chunk.source_id not in self._source_to_idx:
            return

        await self._raw_queue.put(chunk)

    async def _process_chunk(self, chunk: RawChunk) -> None:            
        ch_idx = self._source_to_idx[chunk.source_id]
        pos = self._source_pos[chunk.source_id] # текущая позиция конца записи в буфере по каналу
        chunk_len = len(chunk.raw_data)
        
        # Вычисляем целевую позицию конца чанка на основе времени ПК
        buf_end = int((chunk.mtime - self._start_time) * self._sample_rate) - self._file_written_samples
        buf_start = buf_end - chunk_len
        
        # Если чанк улетел в прошлое
        if buf_start < pos:
            buf_start = pos
            buf_end = pos + chunk_len
        
        if buf_end >= self._buf_triger_level: # достигнут уровень срабатывания, спускаем воду
            data_to_write = self._buffer[:self._buf_target_level, :].copy()

            await self._write(data_to_write)

            self._file_written_samples += self._buf_target_level
            
            # Количество элементов, которое нужно сохранить и перенести в начало
            keep_samples = self._buffer_size - self._buf_target_level

            # Сдвигаем сохраняемую часть буфера в самое начало
            self._buffer[:keep_samples, :] = self._buffer[self._buf_target_level : , :]

            # Очищаем оставшуюся часть буфера нулями
            self._buffer[keep_samples:, :] = 0.0

            # Обновляем позиции записи в каналах
            for key in self._source_pos:
                self._source_pos[key] -= self._buf_target_level            

            # Корректируем позицию chunk в буфере
            buf_start -= self._buf_target_level  
            buf_end -= self._buf_target_level  

        self._buffer[buf_start:buf_end, ch_idx] = chunk.raw_data[:buf_end-buf_start]
        self._source_pos[chunk.source_id] = buf_end # обновляем позицию конца записи в буфере по каналу

    async def _write(self, chunk: np.ndarray) ->None:
        if self.file_handle is None: return
        await self._loop.run_in_executor(None, self.file_handle.write, chunk)        

    async def _stop_recording(self) -> None:
        if not self._is_recording:
            return

        await self._raw_queue.put(None)

        if self._worker_task:
            await self._worker_task
            self._worker_task = None   

    async def _finish_recording(self) -> None:
        if self.file_handle is None: 
            return
                
        max_pos: int = 0
        for key in self._source_pos:
            pos = self._source_pos[key]
            if max_pos < pos:
                max_pos = pos

        if max_pos > 0:
            data_to_write = self._buffer[:max_pos, :].copy()
            await self._write(data_to_write)

        self.file_handle.close()
        self.file_handle = None

    async def _start_processing(self) -> None:
        """Запуск воркера обработки очередей."""
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._processing_loop())

    async def _stop_processing_hard(self) -> None:
        """Остановка воркера."""
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def _processing_loop(self) -> None:
        while self._is_running:
            try:
                chunk = await self._raw_queue.get()

                if chunk is None:
                    await self._finish_recording()
                    self._raw_queue.task_done()
                    break

                await self._process_chunk(chunk)

                self._raw_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Ошибка в воркере записи в файл: {e}")

        if not self.file_handle is None:        
            self.file_handle.close()
            self.file_handle = None 

        self._is_recording = False
        self._is_running = False
        
                            
    async def clean_up(self) -> None:
        await self._stop_processing_hard()
