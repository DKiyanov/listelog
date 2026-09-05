from __future__ import annotations
import asyncio
from typing import Optional, Callable, List, Tuple, Coroutine, Any, Set, Dict
from io import BytesIO
from enum import Enum
from collections import deque

import numpy as np
import time
import soundcard as sc
import soundfile as sf
import threading

from src.models import *
from src.utils import *
import src.vad as vad

SAMPLE_RATE: int = 16000
RAW_CHANK_SIZE: int = 512  # ~32 мс при 16кГц, Silero VAD v6 потребляет только такой размер блока

# В милисекундах
MAX_AUDIO_BLOCK_SIZE_LIGHT_MSEC: int = 7000 # Максимальный расмер блока - разрыв по минимальной тишине
MIN_VOICE_LENGTH_IN_BLOCK_MSEC: int = 3000 # Минимальная длительность голоса в блоке - (на меньшей длительности не возможно получить хороши эмбединг голоса)
MAX_AUDIO_BLOCK_SIZE_HARD_MSEC: int = 20000 # Предельный размер блока
MIN_VOICE_LENGTH_TO_SKIP_MSEC: int = 2000 # Если MAX_AUDIO_BLOCK_SIZE_HARD_MSEC а голоса меньше - блок сбрасывается/не отправляется
SILENCE_LIMIT_MSEC: int = 600 # Длительность тишины для формирования блока
SILENCE_PREV_BLOCK_MSEC: int = 200 # Длительность тишины перед началом блока
SILENCE_POST_BLOCK_MSEC: int = 300 # Длительность тишины в конце блока

# MAX_AUDIO_BLOCK_SIZE_LIGHT_MSEC не работает если не достигнут MIN_VOICE_LENGTH_IN_BLOCK
# 

class DeviceType(Enum):
    MICROPHONE = "microfone"
    LOOPBACK = "loopback"
    FILE = "file"

@dataclass(frozen=True)
class AudioDevice:
    type: DeviceType
    id: str
    name: str



class VoiceBuffer:
    """Накапливает PCM-данные и порождает AudioChunk"""

    def __init__(self, source_id: int, on_chunk: OnVoiceChunk) -> None:     
        self.source_id = source_id
        self.on_chunk: OnVoiceChunk = on_chunk

        ms_chunk_size = RAW_CHANK_SIZE * 1000 / SAMPLE_RATE # длительность блока в милисекундах

        self.prev_chunks_count = int(SILENCE_PREV_BLOCK_MSEC // ms_chunk_size)
        self.post_chunks_count = int(SILENCE_POST_BLOCK_MSEC // ms_chunk_size)
        self.silence_limit_chunks_count = int(SILENCE_LIMIT_MSEC // ms_chunk_size)

        self.prev_silence_chunks: deque = deque(maxlen=self.prev_chunks_count )
        self.post_silence_chunks: list[np.ndarray] = []
        self.voice_buffer: list[np.ndarray] = []

        self.vad: vad.VADProcessor = vad.VADProcessor(model_path=get_work_path("assets/silero_vad.onnx"))

        self.record_start_mtime: int = -1
        self.start_mtime: int = 0
        self.cur_mtime: int = 0

        self.max_voice_buffer_light_chunks_count: int = int(MAX_AUDIO_BLOCK_SIZE_LIGHT_MSEC // ms_chunk_size)
        self.max_voice_buffer_hard_chanks_count: int =  int(MAX_AUDIO_BLOCK_SIZE_HARD_MSEC // ms_chunk_size)

        self.voice_chunk_count: int = 0 
        self.voice_chunk_count_low_limit: int = int(MIN_VOICE_LENGTH_IN_BLOCK_MSEC // ms_chunk_size)
        self.voice_chunk_count_to_skip: int = int(MIN_VOICE_LENGTH_TO_SKIP_MSEC // ms_chunk_size)

        self.zeros_chunks: list[np.ndarray] = []
        for i in range(1, self.post_chunks_count):
            self.zeros_chunks.append(np.zeros(RAW_CHANK_SIZE, dtype=np.float32))  

    def is_voice(self, chunk: np.ndarray) -> bool:
        is_voice: bool = self.vad.voice_detected(chunk)        
        return is_voice
    
    def start_recording(self)->None:
        self.reset()
        self.record_start_mtime = -1

    def add_samples(self, chunk: np.ndarray, mtime: int) -> bool:
        if self.record_start_mtime == -1:
            self.record_start_mtime = mtime

        self.cur_mtime = mtime
        is_voice: bool = self.is_voice(chunk)    

        if is_voice:

            # НАКОПЛЕНИЕ БУФЕРА РЕЧИ
            if not self.voice_buffer:
                # Начаинаем накопление нового буфера
                self.voice_buffer.extend(self.prev_silence_chunks)
                self.prev_silence_chunks.clear()

                self.start_mtime = mtime
            else:
                # Речь продолжается
                if self.post_silence_chunks: # Речь продолжается после паузы
                    post_silence_len = len(self.post_silence_chunks)
                    if len(self.voice_buffer) >= self.max_voice_buffer_light_chunks_count and self.voice_chunk_count >= self.voice_chunk_count_low_limit:
                        # Закрываем текущий блок и начинаем новый
                        if post_silence_len <= self.post_chunks_count + self.prev_chunks_count:
                            self.voice_buffer.extend(self.post_silence_chunks[:post_silence_len // 2])
                            tmp_buf = self.post_silence_chunks[post_silence_len // 2:]
                        else:
                            self.voice_buffer.extend(self.post_silence_chunks[:self.post_chunks_count])
                            tmp_buf = self.post_silence_chunks[post_silence_len - self.prev_chunks_count:]

                        self.flush()

                        self.start_mtime = mtime
                        self.voice_buffer.extend(tmp_buf)
                    else:
                        if post_silence_len <= self.post_chunks_count + self.prev_chunks_count:
                            self.voice_buffer.extend(self.post_silence_chunks)
                        else: # если пауза длинная - оставляем от неё начало и конец
                            self.voice_buffer.extend(self.post_silence_chunks[:self.post_chunks_count])
                            self.voice_buffer.extend(self.post_silence_chunks[post_silence_len - self.prev_chunks_count:])

                    self.post_silence_chunks.clear()

            self.voice_buffer.append(chunk)
            self.voice_chunk_count += 1

        if not is_voice:

            if not self.voice_buffer:  # сохраняем чанк тишины чтоб добавит его перед голосом
                self.prev_silence_chunks.append(chunk)

            if self.voice_buffer:  # сохраняем чанк тишины чтоб добавит его после или между голосом
                self.post_silence_chunks.append(chunk) 

                if len(self.post_silence_chunks) >= self.silence_limit_chunks_count and self.voice_chunk_count >= self.voice_chunk_count_low_limit:
                    self.flush()

        if self.voice_buffer and len(self.voice_buffer) >= self.max_voice_buffer_hard_chanks_count:
            if self.voice_chunk_count >= self.voice_chunk_count_to_skip:
                self.flush()
            else: # слишком мало голоса в тишине - отчищаем буфер
                self.clear()

        return is_voice


    def flush(self) -> None:
        """Отдаёт накопленные данные"""
        if not self.voice_buffer:
            return None

        if len(self.post_silence_chunks) < self.post_chunks_count: # добавляем пустоту если её нехватает до нормы
            self.post_silence_chunks.extend(self.zeros_chunks[: len(self.post_silence_chunks) - self.post_chunks_count ])

        ret_buffer = np.concatenate([*self.voice_buffer, *self.post_silence_chunks[:self.post_chunks_count]], axis=0)

        audio_chunk: VoiceChunk = VoiceChunk(
            source_id=self.source_id,
            ts_start=(self.start_mtime - self.record_start_mtime) // 1000000, # милисекунды от начала записи
            ts_end=(self.cur_mtime - self.record_start_mtime) // 1000000, # милисекунды от начала записи
            raw_data=ret_buffer
        )

        self.clear()

        self.on_chunk(audio_chunk)
        

    def clear(self)-> None:
        self.voice_buffer.clear()
        self.start_mtime = 0
        self.cur_mtime = 0
        self.silence_num = 0
        self.voice_chunk_count = 0
        self.prev_silence_chunks.clear()
        self.post_silence_chunks.clear()

    def reset(self) -> None:
        self.clear()
        self.vad.reset_states()


class AudioSource:
    """Захват звука с конкретного устройства"""

    def __init__(
            self,
            device_type: DeviceType,
            device_id: str,
            chunk_size: int,
            on_chunk: Callable[[int, np.ndarray], None],
            on_break: Callable[[], None],
    ) -> None:
        self.device_type = device_type
        self.device_id: Optional[str] = device_id
        self.chunk_size: int = chunk_size
        self.on_chunk: Callable[[int, np.ndarray], None] = on_chunk
        self.on_break: Callable[[], None] = on_break

        self._is_active: bool = False
        self._task: Optional[asyncio.Task] = None
        self._device_name: str = "Unknown"

        # Инициализация устройства по ID или по умолчанию
        if device_type == DeviceType.MICROPHONE:
            self._mic_device = sc.get_microphone(device_id, include_loopback=False)
            self._device_name = self._mic_device.name

        if device_type == DeviceType.LOOPBACK:
            self._mic_device = sc.get_microphone(device_id, include_loopback=True)
            self._device_name = self._mic_device.name

    async def start(self) -> None:
        """Старт фонового чтения аудио-потока."""
        if self._is_active: return

        self._is_active = True
        self._task = asyncio.create_task(self._capture_loop())

    async def stop(self) -> None:
        """Остановка фонового чтения."""
        if not self._is_active: return

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _capture_loop(self) -> None:
        # soundcard блокирует поток при чтении, выполняем чтение частями в run_in_executor
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

        with self._mic_device.recorder(samplerate=SAMPLE_RATE, blocksize=self.chunk_size) as recorder:
            while self._is_active:
                try:
                    # Чтение данных из звуковой карты
                    chunk: np.ndarray = await loop.run_in_executor(
                        None, recorder.record, self.chunk_size
                    )

                    if len(chunk) == 0 or chunk.shape[0] == 0:
                        continue

                    mtime = time.monotonic_ns()

                    # Преобразование СТЕРЕО -> МОНО (усредняем каналы), если каналов > 1
                    if chunk.ndim > 1 and chunk.shape[1] > 1:
                        mono_chunk: np.ndarray = np.mean(chunk, axis=1).astype(np.float32)  # (512, 2) -> (512,)
                    else:
                        mono_chunk = chunk.flatten().astype(np.float32)  # (512, 1) -> (512,)

                    # Гарантируем, что размер равен ровно 512 (soundcard может вернуть чуть меньше/больше)
                    if len(mono_chunk) != 512:
                        mono_chunk = np.pad(mono_chunk, (0, max(0, 512 - len(mono_chunk))))[
                                     :512]  # Дописываем нулями или обрезаем до 512

                    self.on_chunk(mtime, mono_chunk)

                except asyncio.CancelledError:
                    self._is_active = False
                    break
                except Exception as e:
                    print(f"Ошибка захвата аудио: {e}")
                    self._is_active = False
                    self.on_break()
                    break
        

    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def device_name(self) -> str:
        return self._device_name


class Encoder:
    """Потокобезопасный кодировщик аудио."""

    def __init__(self, output_format: str = "ogg") -> None:
        self.output_format: str = output_format.lower()

    @staticmethod
    def encode(chunk: VoiceChunk) -> bytes:
        """Кодирует сырой массив numpy в формат OGG контейнера."""
        bio: BytesIO = BytesIO()
        # soundfile автоматически упаковывает float32 данные в OGG
        with sf.SoundFile(
            bio, 
            mode='w', 
            format='OGG', 
            subtype='OPUS', 
            samplerate=SAMPLE_RATE, 
            channels=1
        ) as f:
            f.write(chunk.raw_data)
        return bio.getvalue()

    @property
    def content_type(self) -> str:
        return "audio/ogg"


class ProcQueue:
    """Организует очереди обработки блоков и воркер для кодирования и записи."""

    def __init__(self, encoder: Encoder, on_packet_ready: Callable[[EncodedChunk], Coroutine[Any, Any, None]]) -> None:
        self.encoder: Encoder = encoder
        self.on_packet_ready: Callable[[EncodedChunk], Coroutine[Any, Any, None]] = on_packet_ready
        self._raw_queue: asyncio.Queue[VoiceChunk|None] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running: bool = False

    async def put(self, chunk: VoiceChunk) -> None:
        """Поместить сырой блок в Queue1."""
        await self._raw_queue.put(chunk)

    async def start_processing(self) -> None:
        """Запуск воркера обработки очередей."""
        if self._is_running:
            return
        self._is_running = True
        self._worker_task = asyncio.create_task(self._processing_loop())

    async def stop_processing(self) -> None:
        """Остановка воркера."""
        self._is_running = False

        # Пушим None в очередь как сигнал остановки
        await self._raw_queue.put(None)
        
        # Ждем, пока воркер сам завершит работу
        if self._worker_task:
            try:
                await self._worker_task
            except Exception as e:
                print(f"Ошибка при ожидании завершения воркера: {e}")

    async def _processing_loop(self) -> None:
        loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        while self._is_running:
            try:
                # Извлекаем сырой блок из Queue1
                chunk = await self._raw_queue.get()
                if chunk is None:
                    self._raw_queue.task_done()
                    break # Выходим из цикла, завершая таску                    

                # Сжатие выполняется в фоновом пуле, чтобы не фризить event loop
                encoded_bytes: bytes = await loop.run_in_executor(
                    None, self.encoder.encode, chunk
                )

                packet: EncodedChunk = EncodedChunk(
                    source_id=chunk.source_id,
                    ts_start=chunk.ts_start,
                    ts_end=chunk.ts_end,
                    encoded_data=encoded_bytes
                )

                # Передаем готовый пакет дальше (в логику сохранения / Queue2)
                await self.on_packet_ready(packet)
                self._raw_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Ошибка в воркере кодирования: {e}")


OnChangeAudioDevicesList = Callable[[List[AudioDevice]], None]

class AudioDevices:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Реализация паттерна Singleton."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        # Предотвращаем повторную инициализацию при вызове AudioDevices()
        if getattr(self, "_initialized", False):
            return

        self._lock = threading.Lock()
        
        # Кэш устройств по типам
        self._cache: Dict[DeviceType, List[AudioDevice]] = {
            DeviceType.MICROPHONE: [],
            DeviceType.LOOPBACK: []
        }
        
        # Колбэки для каждого типа устройства
        self._callbacks: Dict[DeviceType, Set[Tuple[OnChangeAudioDevicesList, Optional[asyncio.AbstractEventLoop]]]] = {
            DeviceType.MICROPHONE: set(),
            DeviceType.LOOPBACK: set()
        }

        self.mcrofone_exists: bool = False
        self.loopback_exists: bool = False

        # Первичный сбор данных перед запуском потока
        self._update_devices()

        # Запуск фонового потока для периодического опроса
        self._is_running = True
        self._monitor_thread = threading.Thread(target=self._loop, daemon=True)
        self._monitor_thread.start()

        self._initialized = True

    def _fetch_current_devices(self, device_type: DeviceType) -> List[AudioDevice]:
        """Получает актуальный список устройств от soundcard."""
        try:
            if device_type == DeviceType.MICROPHONE:
                devices = sc.all_microphones()
                return [AudioDevice(type=device_type, id=d.id, name=d.name) for d in devices]
            
            elif device_type == DeviceType.LOOPBACK:
                devices = sc.all_microphones(include_loopback=True)
                return [AudioDevice(type=device_type, id=d.id, name=d.name) for d in devices if d.isloopback]
        except Exception:
            # Защита от непредвиденных ошибок работы с аудио-подсистемой
            return self._cache[device_type]
        return []

    def _update_devices(self) -> None:
        """Обновляет кэш и вызывает колбэки при изменениях."""
        for d_type in DeviceType:
            if d_type == DeviceType.FILE: continue

            current_list = self._fetch_current_devices(d_type)
            
            with self._lock:
                old_list = self._cache[d_type]
                # Сравниваем списки (благодаря frozen=True у dataclass сравнение работает корректно)
                if current_list != old_list:
                    self._cache[d_type] = current_list
                    if d_type == DeviceType.MICROPHONE:
                        self.mcrofone_exists = len(current_list) > 0
                    elif d_type == DeviceType.LOOPBACK:
                        self.loopback_exists = len(current_list) > 0
                
                    # Копируем колбэки для вызова вне критической секции locks
                    callbacks_to_call = list(self._callbacks[d_type])
                else:
                    callbacks_to_call = []

            # Вызываем колбэки без удержания lock, чтобы избежать deadlock
            for callback, loop in callbacks_to_call:
                try:
                    if loop is not None and loop.is_running():
                        loop.call_soon_threadsafe(callback, current_list)
                    else:
                        callback(current_list)                    
                except Exception as e:
                    print(f"Ошибка при вызове колбэка для {d_type.value}: {e}")

    def _loop(self) -> None:
        """Фоновый цикл опроса устройств."""
        while self._is_running:
            time.sleep(1.0)
            self._update_devices()

    def get_audio_devices(self, device_type: DeviceType) -> List[AudioDevice]:
        """Возвращает кэшированный список устройств заданного типа."""
        with self._lock:
            return list(self._cache[device_type])

    def reg_change_list_callback(self, device_type: DeviceType, callback: OnChangeAudioDevicesList, and_run: bool = False) -> None:
        """Регистрирует подписку на обновление списка устройств."""
        loop = None
        try:
            # Пытаемся автоматически определить запущенный loop Flet-сессии
            loop = asyncio.get_running_loop() 
        except RuntimeError:
            # Вызывается, если метод вызвали вне асинхронного контекста (в обычном потоке)
            pass

        with self._lock:
            self._callbacks[device_type].add((callback, loop))
            current_state = list(self._cache[device_type])

        # Первичный вызов при подписке
        if and_run:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(callback, current_state)
            else:
                callback(current_state)

    def unreg_change_list_callback(self, device_type: DeviceType, callback: OnChangeAudioDevicesList) -> None:
        """Удаляет подписку (полезно для предотвращения утечек памяти)."""
        with self._lock:
            cb = next((cb for cb in self._callbacks[device_type] if cb[1] == callback), None)
            if cb:
                self._callbacks[device_type].discard(cb)

    def stop(self) -> None:
        """Останавливает фоновый поток мониторинга."""
        self._is_running = False


# Инициализация синглтона
audio_devices = AudioDevices()
