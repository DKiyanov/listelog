import asyncio
from typing import Optional, Callable, Coroutine, Any
from io import BytesIO
from collections import deque

import numpy as np
import soundfile as sf
from deepfilter_stream import DeepFilterModel

import scipy.signal as signal

from src.models import *
import src.audio.vad as vad

SAMPLE_RATE: int = 16000
RAW_CHANK_SIZE: int = 512  # 32 мс при 16кГц, Silero VAD v6 потребляет только такой размер блока

ORIG_SAMPLE_RATE: int = 48000 # Входящий поток, шумоподавление DeepFilterNet3 работает с таким
ORIG_RAW_CHANK_SIZE: int = RAW_CHANK_SIZE * ORIG_SAMPLE_RATE // SAMPLE_RATE # 1536

CHANK_DURATION_MS = RAW_CHANK_SIZE * 1000 // SAMPLE_RATE  # 32 

VAD_VOICE_THRESHOLD: float = 0.5

# Формирование фрагментов
FR_PRE_ROLL = 7          # тишина в начало следующего фрагмента
FR_END_TAIL = 10         # тишина в конец текущего фрагмента
FR_SILENCE_END = 18      # тишина для обычного завершения
FR_MIN_VOICE = 100       # минимум голоса для обычного завершения
FR_MAX_LEN = 625         # предельная длина фрагмента
FR_LONG_LEN = 530        # порог "длинного" фрагмента
FR_MIN_TAIL = 3          # минимальная завершённая тишина для длинного
FR_LONG_SILENCE_SPLIT = FR_END_TAIL + FR_PRE_ROLL  

# Вычисление качества звука - отношение сигнал/шум
SNR_VOICE_LEN_LOW_LIMIT: int = 9  # Длительность голоса для учёта его как сигнал для шумоподавления 
SNR_NOISE_LEN_LOW_LIMIT: int = 9  # Длительность без голоса для учёта её как шум для шумоподавления 

SNR_VOICE_THRESHOLD: float = 0.55
SNR_NOISE_THRESHOLD: float = 0.4
SNR_BAD_THRESHOLD: float = 10.0 # Значение SNR меньше которого обработка звука не имеет смысла

# Зануление участков без голоса
ZR_BEFORE_VOICE: int = 7   # Отступ в чанках перед голосом
ZR_AFTER_VOICE: int = 10   # Отступ в чанках после голоса
ZR_FADE_LEN: int = 1       # в чанках, длина сглаживаемого перехода к зануленияемому участку

def resample(chunk: np.ndarray) -> np.ndarray:
    # Для 48000 -> 16000: up = 1, down = 3
    return signal.resample_poly(chunk, 1, 3)

class VoiceBuffer:
    """Накапливает PCM-данные и порождает AudioChunk"""

    def __init__(self, source_id: int, on_fragment: OnFragment) -> None:     
        self.source_id = source_id
        self.on_fragment: OnFragment = on_fragment

        self.vad: vad.VADProcessor = vad.VADProcessor()

        self.record_start_mtime: int = -1
        self.fragment_start_mtime: int = -1
        self.cur_mtime: int = 0

        self.sound_buffer: list[ExtChunk] = []
        self.prev_silence_chunks: deque = deque(maxlen=FR_PRE_ROLL)

        self.voice_total: int = 0    # суммарное число голосовых ck во фрагменте
        self.silence_tail: int = 0   # длина текущей тишины в хвосте raw_data

    def is_voice(self, orig_chunk: np.ndarray) -> bool:
        chunk = resample(orig_chunk)
        is_voice = self.vad.is_voice(chunk) >= VAD_VOICE_THRESHOLD
        return is_voice
    
    def start_recording(self)->None:
        self.reset()
        self.record_start_mtime = -1

    def stop_recording(self)-> None:
        self._flush()        

    def add_samples(self, orig_chunk: np.ndarray, mtime: int) -> bool:
        if self.record_start_mtime == -1:
            self.record_start_mtime = mtime
        self.cur_mtime = mtime

        chunk = resample(orig_chunk)

        vadr = self.vad.is_voice(chunk)
        is_voice = vadr >= VAD_VOICE_THRESHOLD

        ext_chunk = ExtChunk(orig_chunk, vadr)
            
        if not is_voice and self.sound_buffer:
            self.sound_buffer.append(ext_chunk)
            self.silence_tail += 1
        
        buf_len = len(self.sound_buffer)

        if (( buf_len >= FR_MAX_LEN )
         or ( buf_len >= FR_LONG_LEN and self.silence_tail >= FR_MIN_TAIL and is_voice )
         or ( self.silence_tail >= FR_SILENCE_END and self.voice_total >= FR_MIN_VOICE and not is_voice )
        ):
            next_buf = self._flush()

            if next_buf:
                self.prev_silence_chunks.extend(next_buf)                    

        if is_voice:
            if not self.sound_buffer and self.prev_silence_chunks:
                self.sound_buffer.extend(self.prev_silence_chunks)
                self.prev_silence_chunks.clear()

            if self.fragment_start_mtime == -1:
                self.fragment_start_mtime = mtime - ( len(self.sound_buffer) * CHANK_DURATION_MS * 1000000 )

            self.sound_buffer.append(ext_chunk)
            self.voice_total += 1
            self.silence_tail = 0

        return is_voice
    #-------------------------------------------------------------

    def _flush(self)-> list[ExtChunk]:
        if self.silence_tail == 0:
            cur_buf = self.sound_buffer
            next_buf: list[ExtChunk] = []
        elif self.silence_tail >= FR_LONG_SILENCE_SPLIT:
            cur_buf = self.sound_buffer[: - self.silence_tail + FR_END_TAIL]
            next_buf = self.sound_buffer[- FR_PRE_ROLL:]
        else:
            left_len = ( self.silence_tail + 1 ) // 2
            right_len = self.silence_tail - left_len
            cur_buf = self.sound_buffer[: - right_len]
            next_buf = self.sound_buffer[- right_len:]

        fragment = Fragment(
            source_id = self.source_id,
            ts_start  = (self.fragment_start_mtime - self.record_start_mtime) // 1000000, # милисекунды от начала записи
            ts_end    = (self.cur_mtime - self.record_start_mtime) // 1000000, # милисекунды от начала записи            
            chunks    = cur_buf
        )

        self.on_fragment(fragment)

        self.clear()

        return next_buf

    def clear(self)-> None:
        self.sound_buffer.clear()
        self.prev_silence_chunks.clear()
        self.voice_total = 0
        self.silence_tail = 0
        self.fragment_start_mtime = -1

    def reset(self) -> None:
        self.clear()
        self.vad.reset_states()

def calc_chunk_energy(chunk: np.ndarray) -> float:
    # Энергия чанка (средний квадрат амплитуды)
    return float(np.mean(np.square(chunk)))       

def calc_chunk_rms(chunk: np.ndarray) -> float:
    # Расчет интегральной громкости (RMS) для индикатора активности
    return float(np.sqrt(np.mean(np.square(chunk))))

class Denoiser:
    def __init__(self, model_path:str):
        self.model = DeepFilterModel(model_path= model_path)  
        self.stream = self.model.new_stream()

        self.sample_rate: int = ORIG_SAMPLE_RATE
        self.chunk_size: int = 9600 # из примеров в документации

    def denoise(self, raw_data: np.ndarray) -> np.ndarray:
        parts = []

        for start in range(0, len(raw_data), self.chunk_size):
            chunk = raw_data[start:start + self.chunk_size]

            enhanced = self.stream.process(chunk.astype(np.float32, copy=False), sr=self.sample_rate)

            parts.append(np.asarray(enhanced, dtype=np.float32))

        # Получить остаток, находящийся во внутренних буферах.
        tail = self.stream.flush()
        if tail is not None and len(tail) > 0:
            parts.append(np.asarray(tail, dtype=np.float32))

        if not parts:
            return np.empty(0, dtype=np.float32)

        result = np.concatenate(parts)

        return result

class Encoder:
    """Потокобезопасный кодировщик аудио."""

    def __init__(self, output_format: str = "ogg") -> None:
        self.output_format: str = output_format.lower()

    @staticmethod
    def encode(raw_data: np.ndarray) -> bytes:
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
            f.write(raw_data)
        return bio.getvalue()

    @property
    def content_type(self) -> str:
        return "audio/ogg"

@dataclass(frozen=True)
class _ChunkEnergy:
    is_noice: bool
    energy: float
    ts_pos: int

class _ChanelData:
    def __init__(self):
        self.chunk_energy_list: list[_ChunkEnergy] = []

OnFragmentReady = Callable[[EncodedFragment], Coroutine[Any, Any, None]]

class ProcQueue:
    """Организует очереди обработки блоков и воркер для кодирования и записи."""

    def __init__(self, denoiser: Denoiser, encoder: Encoder,  on_fragmenent_ready: OnFragmentReady) -> None:
        self.denoiser = denoiser
        self.encoder = encoder
        self.on_fragment_ready = on_fragmenent_ready
        self._raw_queue: asyncio.Queue[Fragment|None] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running: bool = False
        self._chanels: dict[int, _ChanelData] = {}

    async def wait(self) -> None:
        await self._raw_queue.join()

    def put(self, chunk: Fragment) -> None:
        """Поместить сырой блок в Queue."""
        self._raw_queue.put_nowait(chunk)

    def start_processing(self) -> None:
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
        self.loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
        while self._is_running:
            try:
                # Извлекаем фрагмент из Queue
                fragment = await self._raw_queue.get()
                if fragment is None:
                    self._raw_queue.task_done()
                    break # Выходим из цикла, завершая таску 

                encoded_fragment = await self._process_fragment(fragment)

                # Передаем готовый пакет дальше (в логику сохранения / Queue2)
                await self.on_fragment_ready(encoded_fragment)
                self._raw_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Ошибка в воркере кодирования: {e}")

    async def _process_fragment(self, fragment: Fragment) -> EncodedFragment:
        audio_data = np.concatenate([item.chunk for item in fragment.chunks])

        # Шумоподавление
        audio_data = self.denoiser.denoise(audio_data)

        # Рассчитать соотношение сигнал шум по vadr
        #is_bad = False
        vadrs = [item.vadr for item in fragment.chunks]
        snr = self._calc_snr(fragment.source_id, fragment.ts_start, audio_data, vadrs, ORIG_RAW_CHANK_SIZE)
        is_bad = snr <= SNR_BAD_THRESHOLD

        # 48кГц -> 16кГц
        audio_data = resample(audio_data)

        # Зануление интервалов с тишиной с сглаживанием краёв
        vad_map = [item.vadr >= VAD_VOICE_THRESHOLD for item in fragment.chunks]
        self._zeroing_noise(audio_data, vad_map, RAW_CHANK_SIZE)

        # Сжатие выполняется в фоновом пуле, чтобы не фризить event loop
        encoded_bytes: bytes = await self.loop.run_in_executor(
            None, self.encoder.encode, audio_data
        )

        packet = EncodedFragment(
            source_id    = fragment.source_id,
            ts_start     = fragment.ts_start,
            ts_end       = fragment.ts_end,
            is_bad       = is_bad,
            encoded_data = encoded_bytes
        )

        return packet
    
    def _calc_snr(self, source_id: int, ts_start: int, audio_data: np.ndarray, vadrs: list[float], chunk_size: int)->float:
        if source_id not in self._chanels:
            self._chanels[source_id] = _ChanelData()
        chanel = self._chanels[source_id]
        low_ts_pos = ts_start - 30000 # 30 сек.
        chanel.chunk_energy_list = [item for item in chanel.chunk_energy_list if item.ts_pos >= low_ts_pos]

        voice_len: int = 0
        noise_len: int = 0 

        ts_pos: int = ts_start
        for i, start_idx in enumerate(range(0, len(audio_data), chunk_size)):
            chunk = audio_data[start_idx : start_idx + chunk_size]
            chunk_energy = calc_chunk_energy(chunk)
            vadr = vadrs[i]

            if vadr >= SNR_VOICE_THRESHOLD:
                voice_len += 1
                if voice_len >= SNR_VOICE_LEN_LOW_LIMIT:
                    chanel.chunk_energy_list.append(_ChunkEnergy(False, chunk_energy, ts_pos))
            else:
                voice_len = 0

            if vadr <= SNR_NOISE_THRESHOLD:
                noise_len += 1
                if noise_len >= SNR_NOISE_LEN_LOW_LIMIT:
                    chanel.chunk_energy_list.append(_ChunkEnergy(True, chunk_energy, ts_pos))
            else:
                noise_len = 0

            ts_pos += CHANK_DURATION_MS

        voice_energy: float = 0.0
        noise_energy: float = 0.0
        voice_count: int = 0
        noise_count: int = 0

        for item in chanel.chunk_energy_list:
            if item.is_noice:
                noise_energy += item.energy
                noise_count  += 1
            else:
                voice_energy += item.energy
                voice_count  += 1
        
        if noise_count == 0 or voice_count == 0:
            snr = 100
        else:
            snr = float(10 * np.log10((voice_energy / voice_count) / (noise_energy / noise_count)))

        return snr

    def _zeroing_noise(self, audio_data: np.ndarray, vad_map: list[bool], chunk_size: int) -> None:
        """
        Зануляет участки тишины в audio_data (in-place).

        - Участки тишины = последовательности чанков, где vad_map == False.
        - С каждого такого участка "отступаем" AFTER_VOICE чанков слева (после голоса)
        и BEFORE_VOICE чанков справа (перед голосом) — эти зоны НЕ трогаем.
        - Середину зануляем.
        - По краям занулённой зоны делаем линейный фейд длиной FADE_LEN чанков.
        """
        n_chunks = len(vad_map)
        assert audio_data.shape[0] == n_chunks * chunk_size
        assert audio_data.ndim == 1

        fade = ZR_FADE_LEN * chunk_size
        min_zone_len = fade * 3 
        ramp_in = np.linspace(1.0, 0.0, fade, dtype=audio_data.dtype)
        ramp_out = np.linspace(0.0, 1.0, fade, dtype=audio_data.dtype)

        # Найдём все участки тишины как полуинтервалы [start, end) в чанках
        i = 0
        while i < n_chunks:
            if vad_map[i]:
                i += 1
                continue

            # начало тишины
            start = i
            while i < n_chunks and not vad_map[i]:
                i += 1
            end = i  # exclusive

            # Границы зануляемой зоны в чанках (после "сужения")
            z_start = start + ZR_AFTER_VOICE
            z_end   = end   - ZR_BEFORE_VOICE

            if z_end <= z_start:
                # участок слишком короткий — нечего занулять
                continue

            # Переводим в сэмплы
            s_start = z_start * chunk_size
            s_end   = z_end   * chunk_size

            # Если фейд не помещается в зону — пропускаем
            zone_len = s_end - s_start
            if zone_len <= min_zone_len:
                continue

            audio_data[s_start + fade : s_end - fade] = 0.0 # середина - зануление
            audio_data[s_start : s_start + fade] *= ramp_in # Фейд-ин слева
            audio_data[s_end - fade : s_end] *= ramp_out    # Фейд-аут справа
