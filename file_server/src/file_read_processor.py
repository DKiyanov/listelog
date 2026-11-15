from src.models import *
from src.file.file_reader import *
from src.audio.audio_queue import *


class FileReadProcessor:
    def __init__(
        self, 
        on_fragment: OnFragment,
    ) -> None:        
        self.on_fragment: OnFragment = on_fragment

        self.file_reader = FileReader(self._on_chunk)
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

    def set_file(self, file_path: str, in_mono:bool, diarize: bool):
        self.file_path = file_path
        self.in_mono = in_mono
        self.diarize = diarize
        self.file_reader.set_file(file_path, in_mono)
        self.channels_count = self.file_reader.channels_count
        self.duration = int(self.file_reader.duration_sec * 1000)
        self.total_duration = self.channels_count * self.duration 
        self.buffers = {i: VoiceBuffer(source_id = i, on_fragment= self._on_self_fragment) for i in range(self.channels_count)}
        self.packet_count = 0

    def start_read(self) -> asyncio.Future[Any]:
        self.is_active = True
        future = self.file_reader.start_read()
        return future

    async def _on_chunk(self, chanel: int, chunk: np.ndarray, mtime: int) -> None:
        buffer = self.buffers[chanel]
        buffer.add_samples(chunk, mtime)
        await asyncio.sleep(0) # даём время на обработку очереди

    def _on_self_fragment(self, fragment: Fragment)-> None:
        self.packet_count += 1
        self.on_fragment(fragment)

    def stop(self)->None:
        if not self.is_active:
            return

        self.is_active = False
        self.file_path = ""
        self.file_reader.cancel_reading()
