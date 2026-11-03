import asyncio
from typing import Optional, Callable, Coroutine, Any
from io import BytesIO

from collections import deque

import numpy as np

import soundfile as sf

import scipy.signal as signal

from src.models import *
import src.audio.vad as vad
from src.audio.noice_reductor import *

SAMPLE_RATE: int = 16000
RAW_CHANK_SIZE: int = 512  # ~32 мс при 16кГц, Silero VAD v6 потребляет только такой размер блока

ORIG_SAMPLE_RATE: int = 48000 # Входящий поток, шумоподавление DeepFilterNet3 работает с таким
ORIG_RAW_CHANK_SIZE: int = RAW_CHANK_SIZE * ORIG_SAMPLE_RATE // SAMPLE_RATE # 1536

# В милисекундах
MAX_AUDIO_BLOCK_SIZE_LIGHT_MSEC: int = 7000 # Максимальный расмер блока - разрыв по минимальной тишине
MIN_VOICE_LENGTH_IN_BLOCK_MSEC: int = 3000 # Минимальная длительность голоса в блоке - (на меньшей длительности не возможно получить хороши эмбединг голоса)
MAX_AUDIO_BLOCK_SIZE_HARD_MSEC: int = 20000 # Предельный размер блока
MIN_VOICE_LENGTH_TO_SKIP_MSEC: int = 2000 # Если MAX_AUDIO_BLOCK_SIZE_HARD_MSEC а голоса меньше - блок сбрасывается/не отправляется
SILENCE_LIMIT_MSEC: int = 600 # Длительность тишины для формирования блока
SILENCE_PREV_BLOCK_MSEC: int = 200 # Длительность тишины перед началом блока
SILENCE_POST_BLOCK_MSEC: int = 300 # Длительность тишины в конце блока

SNR_MIN_VOICE_LINGTH_MSEC: int = 250  # Длительность голоса для учёта его как сигнал для шумоподавления 
SNR_MIN_SILINCE_LINGTH_MSEC: int = 250  # Длительность без голоса для учёта её как шум для шумоподавления 

SNR_VOICE_THRESHOLD: float = 0.55
SNR_NOICE_THRESHOLD: float = 0.4

class VoiceBuffer:
    """Накапливает PCM-данные и порождает AudioChunk"""

    def __init__(self, source_id: int, on_chunk: OnVoiceChunk, denoiser: NoiseReductionWorker | None, no_flush: bool = False) -> None:     
        self.source_id = source_id
        self.on_chunk: OnVoiceChunk = on_chunk
        self.denoiser: NoiseReductionWorker | None = denoiser
        self.no_flush: bool = no_flush

        ms_chunk_size = RAW_CHANK_SIZE * 1000 / SAMPLE_RATE # длительность блока в милисекундах

        self.prev_chunks_count = int(SILENCE_PREV_BLOCK_MSEC // ms_chunk_size)
        self.post_chunks_count = int(SILENCE_POST_BLOCK_MSEC // ms_chunk_size)
        self.silence_limit_chunks_count = int(SILENCE_LIMIT_MSEC // ms_chunk_size)

        self.prev_silence_chunks: deque = deque(maxlen=self.prev_chunks_count )
        self.post_silence_chunks: list[np.ndarray] = []
        self.voice_buffer: list[np.ndarray] = []

        self.vad: vad.VADProcessor = vad.VADProcessor(model_path=vad_model_path)

        self.record_start_mtime: int = -1
        self.start_mtime: int = 0
        self.cur_mtime: int = 0

        self.max_voice_buffer_light_chunks_count: int = int(MAX_AUDIO_BLOCK_SIZE_LIGHT_MSEC // ms_chunk_size)
        self.max_voice_buffer_hard_chanks_count: int =  int(MAX_AUDIO_BLOCK_SIZE_HARD_MSEC // ms_chunk_size)

        self.voice_chunk_count: int = 0 
        self.voice_chunk_count_low_limit: int = int(MIN_VOICE_LENGTH_IN_BLOCK_MSEC // ms_chunk_size)
        self.voice_chunk_count_to_skip: int = int(MIN_VOICE_LENGTH_TO_SKIP_MSEC // ms_chunk_size)

        self.snr_voice_chunk_count: int = 0
        self.snr_silince_chunk_count: int = 0         
        self.snr_voice_chunk_count_low_limit: int = int(SNR_MIN_VOICE_LINGTH_MSEC // ms_chunk_size)
        self.snr_silince_chunk_count_low_limit: int = int(SNR_MIN_SILINCE_LINGTH_MSEC // ms_chunk_size)

        self.zeros_chunks: list[np.ndarray] = []
        for i in range(1, self.post_chunks_count):
            self.zeros_chunks.append(np.zeros(RAW_CHANK_SIZE, dtype=np.float32))  

        self.orig_sampe_rate: int = 48000
        self.orig_buffer: list[np.ndarray] = []
        self.energies_buffer_size_chunks = 94 # ~ 3 сек

        # Очереди для хранения энергии (RMS квадрат) чанков
        self.noise_energies = deque(maxlen=self.energies_buffer_size_chunks)
        self.voice_energies = deque(maxlen=self.energies_buffer_size_chunks)  

    def _resample(self, chunk: np.ndarray) -> np.ndarray:
        # Для 48000 -> 16000: up = 1, down = 3
        return signal.resample_poly(chunk, 1, 3)

    def is_voice(self, orig_chunk: np.ndarray) -> tuple[bool, float]:
        chunk_energy = self._calc_ms(orig_chunk)
        chunk = self._resample(orig_chunk)
        is_voice, _ = self.vad.voice_detected(chunk)        
        return is_voice, float(np.sqrt(chunk_energy))
    
    def start_recording(self)->None:
        self.reset()
        self.record_start_mtime = -1

    def add_samples(self, orig_chunk: np.ndarray, mtime: int) -> tuple[bool, float]:
        if self.record_start_mtime == -1:
            self.record_start_mtime = mtime
        self.cur_mtime = mtime

        if self.denoiser:
            self.orig_buffer.append(orig_chunk)

        chunk_energy = self._calc_ms(orig_chunk)

        chunk = self._resample(orig_chunk)

        is_voice, speech_prob = self.vad.voice_detected(chunk)   

        if speech_prob >= SNR_VOICE_THRESHOLD:
            self.snr_silince_chunk_count = 0
            self.snr_voice_chunk_count += 1
            if self.snr_voice_chunk_count >= self.snr_silince_chunk_count_low_limit:
                self.voice_energies.append(chunk_energy)
        
        if speech_prob <= SNR_NOICE_THRESHOLD:
            self.snr_voice_chunk_count = 0
            self.snr_silince_chunk_count += 1
            if self.snr_silince_chunk_count >= self.snr_silince_chunk_count_low_limit:
                self.noise_energies.append(chunk_energy)

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

        return is_voice, float(np.sqrt(chunk_energy))


    def flush(self, ignore_no_flush: bool = False) -> None:
        """Отдаёт накопленные данные"""        
        if not self.voice_buffer:
            return None

        if self.no_flush and not ignore_no_flush:
            return

        if len(self.post_silence_chunks) < self.post_chunks_count: # добавляем пустоту если её нехватает до нормы
            self.post_silence_chunks.extend(self.zeros_chunks[: len(self.post_silence_chunks) - self.post_chunks_count ])

        ret_buffer = np.concatenate([*self.voice_buffer, *self.post_silence_chunks[:self.post_chunks_count]], axis=0)

        snr: float = self._calc_snr()

        # if self.denoiser:
        #     if snr < 20: #Требуется шумоподавление
        #         print(f"denoise snr = {snr}")
        #         orig_audio_chunk = VoiceChunk(
        #             source_id=self.source_id,
        #             ts_start=(self.start_mtime - self.record_start_mtime) // 1000000, # милисекунды от начала записи
        #             ts_end=(self.cur_mtime - self.record_start_mtime) // 1000000, # милисекунды от начала записи
        #             is_bad=False,
        #             raw_data= np.concatenate(self.orig_buffer)
        #         )

        #         self.denoiser.add_audio(orig_audio_chunk)

        #         self.clear()
        #         return

        # print(f"snr = {snr}")
        audio_chunk = VoiceChunk(
            source_id=self.source_id,
            ts_start=(self.start_mtime - self.record_start_mtime) // 1000000, # милисекунды от начала записи
            ts_end=(self.cur_mtime - self.record_start_mtime) // 1000000, # милисекунды от начала записи            
            is_bad= snr < 10, # если snr < 10 - плохое качество
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
        self.orig_buffer.clear()

    def reset(self) -> None:
        self.clear()
        self.vad.reset_states()

    def _calc_ms(self, chunk: np.ndarray) -> float:
        # Энергия чанка (средний квадрат амплитуды)
        # Предотвращаем деление на ноль или логарифм от нуля
        return float(np.mean(np.square(chunk))) + 1e-12 

    def _calc_snr(self)-> float:
        # возвращает SNR от уастков с голосом и без голоса
        snr: float = 0.0 
        if len(self.voice_energies) > 0 and len(self.noise_energies) > 0:
            # Средняя энергия по всем сохраненным чанкам за 3 секунды
            mean_voice_energy = np.mean(self.voice_energies)
            mean_noise_energy = np.mean(self.noise_energies)
            
            # Стандартная формула SNR в децибелах: 10 * log10(P_signal / P_noise)
            snr = float(10 * np.log10(mean_voice_energy / mean_noise_energy))

        return snr        


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
                    is_bad=chunk.is_bad,
                    encoded_data=encoded_bytes
                )

                # Передаем готовый пакет дальше (в логику сохранения / Queue2)
                await self.on_packet_ready(packet)
                self._raw_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Ошибка в воркере кодирования: {e}")
