import soundfile as sf
from typing import Any, List, Dict
from dataclasses import asdict
import numpy as np
from pathlib import Path
import pickle

from listelog_pyworker_lib.file_processor import *

from faster_whisper import WhisperModel

def _get_audio_file_content(audio_file_path: str | Path) -> np.ndarray:
    """Читает весь контент аудиофайла ogg opus и возвращает массив сэмплов float32."""
    data: np.ndarray
    sample_rate: int
    
    data, sample_rate = sf.read(audio_file_path, dtype="float32")
    return data

@dataclass(frozen=True)
class _SttConfig(AbstractConfig):
    model_path: str
    device: str
    compute_type: str
    language: str
    max_duration_kf : float
    max_duration_add: float

    def __init__(self, config_path: str) -> None:
        super().__init__(config_path)  

class SttProcessorWhisper(FileProcessor):
    def __init__(self, main_config: MainConfig, config_path:str) -> None:
        self._main_config = main_config
        self._config = _SttConfig(config_path)
        self.work_types = "stt"
        self.next_work_type = ""           
        self.sample_rate = 16000

        self.model: WhisperModel = WhisperModel(
            model_size_or_path=self._config.model_path,
            device=self._config.device,
            compute_type=self._config.compute_type
        )        

    def get_work_types(self) -> str:
        return self.work_types

    async def process_file(self, task: Task) -> str:
        if task.work_type != self.work_types:
            return ""
            
        self._init_file(task.sid, task.cid)
        audio_data = _get_audio_file_content(task.original_file)

        max_proc_duration_sec = (len(audio_data) / self.sample_rate) * self._config.max_duration_kf + self._config.max_duration_add
        task.start_processing(max_proc_duration_sec)

        result = self._porcess_stt(audio_data)

        task.save_sttd_result(result, self.spks)

        return self.next_work_type

    def _porcess_stt(self, audio_data: np.ndarray) -> list[RecognitionResult]:
        self._load_context()
        text =self._get_text(audio_data, self.context)
        self._add_context(text)
        self._save_context()
        audio_length_ms = len(audio_data) * 1000 // self.sample_rate
        result = RecognitionResult(
            self.chunk_info.tss, 
            self.chunk_info.tss + audio_length_ms, 
            self.spkid, 
            text
        )
        return [result]

    def _get_text(
        self, 
        audio_buffer: np.ndarray,
        context: str,
    ) -> str:

        # Проверка на пустой буфер во избежание ошибок декодера
        if audio_buffer is None or audio_buffer.size == 0:
            return ""

        # Настройка параметров транскрибации
        kwargs: Dict[str, Any] = {
            "language": self._config.language,
            "vad_filter": True,
            "initial_prompt": context if context.strip() else None  # Передача контекста беседы
        }

        # Вызов генератора транскрибации
        # segments - это итератор, beam_size=5 является стандартом для хорошего баланса скорость/качество
        segments, _ = self.model.transcribe(
            audio=audio_buffer,
            beam_size=5,
            **kwargs
        )

        # Сборка текста из сегментов
        text_segments: List[str] = [segment.text for segment in segments]
        
        # Объединяем сегменты и очищаем от лишних пробелов по краям
        return "".join(text_segments).strip()

    def _load_context(self): 
        """Загружает данные из файла, ранее сохранённые с помощью save_for_next."""
        if not self.context_path.exists():
            self.context = ""
            return
        
        with open(self.context_path, 'rb') as f:
            self.context = pickle.load(f)
            return     

    def _save_context(self) -> None: 
        """Сохраняет context и window_list в файл с максимальной скоростью."""
        data = self.context
        with open(self.context_path, 'wb') as f:
            # pickle.HIGHEST_PROTOCOL обеспечивает максимальную скорость и сжатие бинарных данных
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)

    def _add_context(self, text: str):
        txt = self.context + " " + text
        words = txt.split()
        self.context = " ".join(words[-100:])

    def _init_file(self, sid: str, cid: str) -> None:

        session_dir = Path(self._main_config.sessions_dir) / sid     

        self.chunk_info_path: str = str(session_dir / "info" / f"{cid}_info.json")

        with open(self.chunk_info_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            self.chunk_info = ChunkInfo(**data)   

        self.spkid = f"{self.chunk_info.src}/1"  
        self.spks = [RecognitiontSpeaker(self.spkid, "", "", True, False)]   

        self.context_path = session_dir / "context.ctdt"
