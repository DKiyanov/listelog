import asyncio

from src.models import *
from src.init import *
from src.audio.vad import VADProcessor
from src.audio.audio_queue import *
from src.net.net_client import *
from src.file.file_reader import *

from src.file_read_processor import *

class AudioSender:
    def __init__(self, net_client: NetClient) -> None:
        self.is_recording_on = False
        self.file_eof = False
        self.recording_source_list: list[RecordSourceInfo] = []
        self.recording_source_ids = set()

        VADProcessor.init(model_path=vad_model_path)
        self.denoiser = Denoiser(denoiser_model_path)
        self.encoder: Encoder = Encoder(output_format="ogg")
        self.proc_queue: ProcQueue = ProcQueue(denoiser=self.denoiser, encoder=self.encoder, on_fragmenent_ready=self._on_encoded_fragmentent)
        self.net_client = net_client

        self.audio_packet_num: int = 0  # глобальный счётчик отправляемых на сервер пакетов
        self.save_file_path: str = ""
        self.title: str = ""

        self.file_reader = FileReadProcessor(self._on_fragment)       

    async def file_upload(self, file_path:str, title: str, in_mono:bool, diarize: bool) -> str:
        print("Start upload")
        self.title = title

        self.file_reader.set_file(file_path, in_mono, diarize)

        self.recording_source_list.clear()
        for channel in range(self.file_reader.channels_count):
            self.recording_source_list.append(RecordSourceInfo(channel, self.file_reader.diarize))

        if not self.recording_source_list:
            raise ValueError("file is invalid")

        self.proc_queue.start_processing()

        await self.start_recording_ex(self.title)

        sid = self.net_client.current_sid
        if not sid:
            raise ValueError("failed to start a data transfer session to the server")

        future = self.file_reader.start_read()
        await future

        await self.proc_queue.wait()

        await self.stop_recording()

        print("Finish upload")

        return sid

    def _on_fragment(self, chunk: Fragment) -> None:
        if chunk.source_id in self.recording_source_ids:
            self.proc_queue.put(chunk)

    async def _on_encoded_fragmentent(self, packet: EncodedFragment) -> None:
        source = next((source for source in self.recording_source_list if source.source_id == packet.source_id), None)
        if source == None: return

        self.audio_packet_num += 1        
        await self.net_client.send_packet(packet, self.audio_packet_num, source.diarize)
        print(f"on_encoded_fragmentent {self.audio_packet_num}")
      
    async def start_recording_ex(self, title: str) -> None:
        self.recording_source_ids = {source.source_id for source in self.recording_source_list}

        await self.net_client.start_recording(title)

        self.is_recording_on = True
        self.file_eof = False

    async def stop_recording(self) -> None:
        self.file_reader.stop()

        await self.net_client.stop_recording()

        self.is_recording_on = False
        self.file_eof = False
        self.recording_source_list.clear()
        self.recording_source_ids.clear()        

    def _on_file_finish(self) -> None:
        # Вызывается при завершении чтения файла
        self.file_eof = True      
        