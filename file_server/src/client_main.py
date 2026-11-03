import asyncio

from src.models import *
from src.audio.audio_queue import *
from src.file.file_saver import *
from src.net.net_client import *
from src.file.file_reader import *

from src.file_read_processor import *
from src.audio.noice_reductor import *

class AudioSender:
    def __init__(self, net_client: NetClient) -> None:
        self.is_recording_on = False
        self.file_eof = False
        self.recording_source_list: list[RecordSourceInfo] = []
        self.recording_source_ids = set()

        self.encoder: Encoder = Encoder(output_format="ogg")
        self.proc_queue: ProcQueue = ProcQueue(encoder=self.encoder, on_packet_ready=self._on_encoded_chunk)
        self.file_saver = FileSaver()
        self.net_client = net_client

        self.audio_packet_num: int = 0  # глобальный счётчик отправляемых на сервер пакетов
        self.save_file_path: str = ""
        self.title: str = ""

        self.denoiser = NoiseReductionWorker(denoise_model_path, self._on_voice_chunk)
        self.denoiser.start()

        self.file_reader = FileReadProcessor(self.denoiser, self._on_file_read_start, self._on_voice_chunk, self._on_file_finish)
        self.file_upload_future: asyncio.Future[Any] | None

    async def file_upload(self, file_path:str, title: str, save_audio_path:str, in_mono:bool, diarize: bool) -> str:
        self.save_file_path = save_audio_path
        self.title = title

        loop = asyncio.get_running_loop()
        self.file_upload_future = loop.create_future()
        
        self.file_reader.start_read(file_path, in_mono, diarize)

        return await self.file_upload_future

    def _on_raw_chank(self, chunk: RawChunk) -> None:
        if self.save_file_path == "": return
        if self.is_recording_on and chunk.source_id in self.recording_source_ids:
            asyncio.create_task(self.file_saver.add_chunk(chunk))

    def _on_voice_chunk(self, chunk: VoiceChunk) -> None:
        """Callback: Буфер накопил и отдал VoiceChunk"""
        if self.is_recording_on and chunk.source_id in self.recording_source_ids:
            asyncio.create_task(self.proc_queue.put(chunk))

    async def _on_encoded_chunk(self, packet: EncodedChunk) -> None:
        """Callback: Пакет сжат в OGG и готов к обработке"""
        source = next((source for source in self.recording_source_list if source.source_id == packet.source_id), None)
        if source == None: return

        self.audio_packet_num += 1        
        await self.net_client.send_packet(packet, self.audio_packet_num, source.diarize)

    def _on_file_read_start(self) -> None:
        self.recording_source_list.clear()

        for channel in range(self.file_reader.channels_count):
            self.recording_source_list.append(RecordSourceInfo(channel, self.file_reader.diarize))

        if not self.recording_source_list:
            return
        
        asyncio.create_task(self.start_recording_ex(self.title))
      
    async def start_recording_ex(self, title: str) -> None:
        self.recording_source_ids = {source.source_id for source in self.recording_source_list}

        await self.net_client.start_recording(title)
        if self.save_file_path != "":
            await self.file_saver.start_recording(self.save_file_path, self.recording_source_ids, SAMPLE_RATE) 

        self.is_recording_on = True
        self.file_eof = False

    async def stop_recording(self) -> None:
        """Остановка записи и обработка остатков буфера."""
        self.file_reader.stop()

        await self.file_saver.stop_recording()
        await self.net_client.stop_recording()

        self.is_recording_on = False
        self.file_eof = False
        self.recording_source_list.clear()
        self.recording_source_ids.clear()

        if self.file_upload_future and not self.file_upload_future.done():
            self.file_upload_future.set_result("") 
            self.file_upload_future = None         

    def on_send(self):
        if self.file_eof and self.net_client.sended_count == self.file_reader.packet_count:
            asyncio.create_task(self.stop_recording())        

    def _on_file_finish(self) -> None:
        # Вызывается при завершении чтения файла
        self.file_eof = True      
        