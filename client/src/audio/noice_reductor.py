import queue
import threading
import asyncio

from deepfilter_stream import DeepFilterModel

from src.models import *

class NoiseReductionWorker:
    def __init__(self, model_path:str, on_chunk: OnVoiceChunk):
        self.on_parent_chunk: OnVoiceChunk = on_chunk

        self.model = DeepFilterModel(model_path= model_path)  
        self.stream = self.model.new_stream()

        from src.audio.audio_queue import VoiceBuffer, ORIG_SAMPLE_RATE, ORIG_RAW_CHANK_SIZE
        self.voice_buffer = VoiceBuffer(-1, on_chunk=self._on_chunk, denoiser=None, no_flush=True)
        self.voice_buffer_chunk_size = ORIG_RAW_CHANK_SIZE

        self.sample_rate: int = ORIG_SAMPLE_RATE
        self.chunk_size: int = 9600 # из примеров в документации

        self._queue = queue.Queue()
        self._is_running = False
        self._thread = None
        self._lock = threading.Lock()



    def add_audio(self, audio_data: VoiceChunk):
        """Добавляет audio_data в очередь на обработку."""
        with self._lock:
            if not self._is_running:
                return
            self._queue.put(audio_data)

    def start(self):
        """Запускает обработку очереди."""
        self.loop = asyncio.get_running_loop()
        with self._lock:
            if self._is_running:
                return  
            self._is_running = True

            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        """Останавливает обработку очереди."""
        with self._lock:
            if not self._is_running:
                return
            
            self._is_running = False
            
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break
                    
            self._queue.put(None) 
            
        if self._thread:
            self._thread.join()
            self._thread = None

    def _loop(self):
        """Внутренний цикл фонового потока."""
        while True:
            try:
                audio_data = self._queue.get()
                
                if audio_data is None or not self._is_running:
                    if audio_data is None:
                        self._queue.task_done()
                    break
                
                self._noise_reductor(audio_data)
                self._queue.task_done()
                
            except Exception as e:
                # Защита фонового потока от падения при ошибке в _noise_reductor
                print(f"Ошибка при обработке аудио: {e}")

    def _noise_reductor(self, audio_data: VoiceChunk):
        raw_data = self._denoise(audio_data.raw_data)

        mtime: int = audio_data.ts_start * 1000000
        mtime_add: int = self.voice_buffer_chunk_size * 1000000000 // self.sample_rate
        self.current_audio_data: VoiceChunk = audio_data

        for start in range(0, len(raw_data), self.voice_buffer_chunk_size):
            chunk = raw_data[start:start + self.voice_buffer_chunk_size]
            self.voice_buffer.add_samples(chunk, mtime)
            mtime += mtime_add

        self.voice_buffer.flush(ignore_no_flush=True)

    def _on_chunk(self, chunk: VoiceChunk) -> None:
        chunk.source_id = self.current_audio_data.source_id
        chunk.ts_start  = self.current_audio_data.ts_start
        chunk.ts_end    = self.current_audio_data.ts_end

        self.loop.call_soon_threadsafe(self.on_parent_chunk, chunk)
        #self.on_parent_chunk(chunk)

    def _denoise(self, audio: np.ndarray) -> np.ndarray:
        parts = []

        for start in range(0, len(audio), self.chunk_size):
            chunk = audio[start:start + self.chunk_size]

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

