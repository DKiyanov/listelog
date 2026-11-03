from src.models import *
from src.file.file_reader import *
from src.audio.audio_queue import *
from src.audio.noice_reductor import *

class FileReadProcessor:
    def __init__(
        self, 
        denoiser: NoiseReductionWorker | None,
        on_start: SimpleCallBack,
        on_voice_chunk: OnVoiceChunk,
        on_finish: SimpleCallBack,
    ) -> None:        
        self.denoiser = denoiser

        self.on_start_parent: SimpleCallBack = on_start
        self.on_finish_parent: SimpleCallBack = on_finish
        self.on_voice_chunk: OnVoiceChunk = on_voice_chunk

        self.file_reader = FileReader(self._on_start, self._on_chunk, self._on_finish)
        self.buffers: dict[int, VoiceBuffer]

        self.is_active = False
        self.packet_count: int = 0

        self.channels_count = 0
        self.duration: int = 0    
        self.total_duration: int = 0 # self.channels_count * self.duration
                
        # Флаг для отслеживания прерывания таски загрузки
        self.upload_task = None

        self.in_mono = False
        self.diarize = False

    def start_read(self, file_path: str, in_mono: bool, diarize: bool):
        self.file_path = file_path
        self.in_mono = in_mono
        self.diarize = diarize
        self.file_reader.read_file(self.file_path, self.in_mono)

    def _on_start(self, channels_count: int, duration: int) -> None:
        self.is_active = True
        self.channels_count = channels_count
        self.duration = duration
        self.total_duration = self.channels_count * self.duration 
        self.buffers = {i: VoiceBuffer(source_id = i, on_chunk= self._on_self_voice_chunk, denoiser = self.denoiser) for i in range(channels_count)}
        self.packet_count = 0
        self.on_start_parent()

    def _on_chunk(self, chanel: int, chunk: np.ndarray, mtime: int) -> None:
        buffer = self.buffers[chanel]
        buffer.add_samples(chunk, mtime) 

    def _on_self_voice_chunk(self, voice_chunk: VoiceChunk)-> None:
        self.packet_count += 1
        self.on_voice_chunk(voice_chunk)
                    
    def _on_finish(self)->None:
        self.on_finish_parent()

    def stop(self)->None:
        if not self.is_active:
            return

        self.is_active = False
        self.file_path = ""
        self.file_reader.cancel_reading()
