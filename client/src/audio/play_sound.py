import pygame

from src.audio.my_audio import *
from src.utils import *

import asyncio
import io

import soundfile as sf
import numpy as np

_has_sound: bool = False
_test_sound: pygame.mixer.Sound | None

def _on_change_audio_devices_list(devices: List[AudioDevice]) -> None: 
    print(f"loopback list changed {len(devices)}")
    global _has_sound, _test_sound, _pygame_mixer_init

    _has_sound = False 
    try:
        pygame.mixer.quit()  
              
        if devices:
            pygame.mixer.init()
            _pygame_mixer_init = True
            _test_sound = pygame.mixer.Sound(get_work_path("assets/test_speaker_sound.mp3"))            
            _has_sound = True
    except pygame.error as e:
        print(f"Audio device not found: {e}")
        _has_sound = False

# регистрируем callback для обработки изменения списка устройств и вызываем его
audio_devices.reg_change_list_callback(DeviceType.LOOPBACK, _on_change_audio_devices_list, True)

def play_test_sound()->None:
    if not _has_sound or not _test_sound:
        return

    _test_sound.play()

async def play_sound_from_buffer(audio_buffer: io.BytesIO, from_sec: float, to_sec: float) -> None:
    if not _has_sound: 
        return

    # Получаем текущие настройки уже запущенного микшера Pygame
    mixer_settings = pygame.mixer.get_init()
    if not mixer_settings:
        return  # Микшер вообще не инициализирован в проекте
        
    target_sr, _, target_channels = mixer_settings
    # В pygame стерео — это 2, моно — 1. Если микшер выдал отрицательное значение (например -2), берем модуль.
    target_channels = abs(target_channels)

    audio_buffer.seek(0)

    def process_audio_range():
        # Читаем файл, принудительно приводя его к количеству каналов микшера
        with sf.SoundFile(audio_buffer) as f:
            file_sr = f.samplerate
            
            # Считаем границы в координатах частоты ИСХОДНОГО файла
            start_frame = int(from_sec * file_sr)
            end_frame = int(to_sec * file_sr)
            
            start_frame = max(0, min(start_frame, len(f)))
            end_frame = max(start_frame, min(end_frame, len(f)))
            
            f.seek(start_frame)
            frames_to_read = end_frame - start_frame
            
            if frames_to_read <= 0:
                return None
                
            # Читаем данные. soundfile автоматически раскопирует моно в стерео, 
            # если target_channels=2, а файл монофонический.
            data = f.read(frames_to_read, dtype='int16', always_2d=True)
            
            # Если целевое количество каналов моно (1), берем только первый канал
            if target_channels == 1 and data.shape[1] > 1:
                data = data[:, 0:1]
            elif target_channels == 2 and data.shape[1] == 1:
                # На всякий случай дублируем, если always_2d не отработал
                data = np.repeat(data, 2, axis=1)

            # РЕСЕМПЛИНГ: Если частота OGG отличается от частоты микшера Pygame
            if file_sr != target_sr:
                # Вычисляем сколько сэмплов должно получиться для корректной скорости
                duration_sec = frames_to_read / file_sr
                new_frames_count = int(duration_sec * target_sr)
                
                if new_frames_count <= 0:
                    return None
                    
                # Быстрый ресемплинг через индексы NumPy
                indices = np.linspace(0, frames_to_read - 1, new_frames_count).astype(int)
                data = data[indices]

            # Превращаем финальный массив в сырые байты
            return data.tobytes()

    # Выполняем обработку в потоке
    raw_bytes = await asyncio.to_thread(process_audio_range)

    if not raw_bytes:
        return

    # Создаем Sound БЕЗ перезапуска микшера
    sound = pygame.mixer.Sound(buffer=raw_bytes)
    
    channel = await asyncio.to_thread(sound.play)
    if not channel:
        return

    while await asyncio.to_thread(channel.get_busy):
        await asyncio.sleep(0.05)
        
