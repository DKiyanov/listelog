import asyncio
import numpy as np
import ffmpeg # !устанавливаемая библиотека ffmpeg-python
from typing import Callable, Optional
import subprocess
import sys

from src.models import *
from src.audio.audio_queue import *

# Патч для скрытия окна терминала при использовании ffmpeg-python на Windows
if sys.platform == "win32":
    # Переопределяем метод run_async, добавляя флаг CREATE_NO_WINDOW
    original_run_async = ffmpeg._run.run_async
    
    def patched_run_async(*args, **kwargs):
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        return original_run_async(*args, **kwargs)
    
    ffmpeg._run.run_async = patched_run_async

# Определение типа для колбэка
FileChunkCallback = Callable[[int, np.ndarray, int], Coroutine[Any, Any, None]]
FileReadStartCallback = Callable[[int, int], None]

class FileReader:
    def __init__(
        self, 
        on_chunk: FileChunkCallback,
    ) -> None:
        self.on_chunk: FileChunkCallback = on_chunk

        self._sample_rate: int = ORIG_SAMPLE_RATE
        self._chunk_size: int = ORIG_RAW_CHANK_SIZE
        self._bytes_per_sample: int = 4  # PCM (float32)

        self._is_reading_on: bool = False

    def _get_file_info(self, file_path: str):
        """Получает длительность файла в мс и количество аудиоканалов."""
        probe: dict = ffmpeg.probe(file_path)
        audio_stream: Optional[dict] = next(
            (stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"), 
            None
        )
        
        if not audio_stream:
            raise ValueError("Аудиопоток не найден в указанном файле.")
            
        self.duration_sec: float = float(probe.get("format", {}).get("duration", 0.0))
        self.channels_count: int = int(audio_stream.get("channels", 2))  # по умолчанию стерео

        self.out_channels_count: int = 1 if self.in_mono else self.channels_count

    def _process_file_sync(self, loop: asyncio.AbstractEventLoop) -> None:
        """Синхронный метод чтения и генерации чанков (выполняется в потоке)."""     

        # Формируем аргументы для output. Если in_mono=True, добавляем ac=1
        output_kwargs = {
            "format": "f32le",
            "acodec": "pcm_f32le",
            "ar": str(self._sample_rate)
        }
        if self.in_mono:
            output_kwargs["ac"] = "1" # Force ffmpeg to downmix to mono

        # Настройка ffmpeg: извлечение аудио, ресемплинг, формат f32le
        process = (
            ffmpeg
            .input(self.file_path)
            .output("pipe:", **output_kwargs)
            .global_args("-nostdin", "-loglevel", "error")
            .run_async(pipe_stdout=True) # , pipe_stderr=subprocess.DEVNULL
        )

        # Размер одного чанка в байтах для одного канала
        channel_chunk_bytes: int = self._chunk_size * self._bytes_per_sample
        # Общий размер считываемого блока (зависит от выходного количества каналов)
        frame_bytes: int = channel_chunk_bytes * self.out_channels_count

        chunk_num: int = 0
        limit_chunk_num: int = int((self.duration_sec * self._sample_rate) // self._chunk_size)
        
        sample_count: int = 0
        self._is_reading_on = True

        try:
            while self._is_reading_on:
                if chunk_num >= limit_chunk_num:
                    print("file read last chunk")

                in_bytes: bytes = process.stdout.read(frame_bytes)
                if not in_bytes:
                    break
                
                # Если считался неполный кадр, дополняем нулями
                if len(in_bytes) < frame_bytes:
                    in_bytes += b'\x00' * (frame_bytes - len(in_bytes))

                # Конвертируем байты в numpy массив 
                raw_data: np.ndarray = np.frombuffer(in_bytes, dtype=np.float32)
                
                # Разделяем каналы (пересобираем массив в матрицу [сэмплы, выходные_каналы])
                interleaved_data: np.ndarray = raw_data.reshape(-1, self.out_channels_count)
                
                # Текущее время чанка от начала файла в нано-секундах
                mtime: int = int((sample_count / self._sample_rate) * 1000000000)

                # Отправляем данные в колбэк
                for channel_idx in range(self.out_channels_count):
                    channel_chunk: np.ndarray = interleaved_data[:, channel_idx]
                    future = asyncio.run_coroutine_threadsafe(self.on_chunk(channel_idx, channel_chunk, mtime), loop)
                    future.result()

                sample_count += self._chunk_size
                chunk_num += 1
        finally:
            process.stdout.close()

            if process.stdin:
                process.stdin.close()
            if process.stderr:
                process.stderr.close()

            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            print("read file finished")

    def set_file(self, file_path: str, in_mono:bool) -> None:
        self.file_path = file_path
        self.in_mono = in_mono
        self._get_file_info(file_path)

    def start_read(self) -> asyncio.Future[Any]:
        """Асинхронный запуск чтения файла без блокировки GUI."""     
        # Запускаем тяжелую операцию декодирования в отдельном потоке
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        future = loop.run_in_executor(None, self._process_file_sync, loop)
        return future

    def cancel_reading(self)->None:
        self._is_reading_on = False
