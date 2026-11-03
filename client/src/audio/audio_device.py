import asyncio
from typing import Optional, Callable, List, Tuple, Set, Dict
from enum import Enum

import numpy as np
import time
import soundcard as sc
import threading

from src.models import *

class DeviceType(Enum):
    MICROPHONE = "microfone"
    LOOPBACK = "loopback"
    FILE = "file"

@dataclass(frozen=True)
class AudioDevice:
    type: DeviceType
    id: str
    name: str

class AudioSource:
    """Захват звука с конкретного устройства"""

    def __init__(
            self,
            device_type: DeviceType,
            device_id: str,
            chunk_size: int,
            sample_rate: int,
            on_chunk: Callable[[int, np.ndarray], None],
            on_break: Callable[[], None],
    ) -> None:
        self.device_type = device_type
        self.device_id: Optional[str] = device_id
        self.chunk_size: int = chunk_size
        self.sample_rate: int = sample_rate
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

        with self._mic_device.recorder(samplerate=self.sample_rate, blocksize=self.chunk_size) as recorder:
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
                        mono_chunk: np.ndarray = np.mean(chunk, axis=1).astype(np.float32)  # (chunk_size, 2) -> (chunk_size,)
                    else:
                        mono_chunk = chunk.flatten().astype(np.float32)  # (chunk_size, 1) -> (chunk_size,)

                    # Гарантируем, что размер равен ровно chunk_size (soundcard может вернуть чуть меньше/больше)
                    if len(mono_chunk) != self.chunk_size:
                        mono_chunk = np.pad(mono_chunk, (0, max(0, self.chunk_size - len(mono_chunk))))[:self.chunk_size]  # Дописываем нулями или обрезаем до chunk_size

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
