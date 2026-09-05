from dataclasses import dataclass
import numpy as np

@dataclass(frozen=True)
class SttdSegment:
    start: float
    end: float
    speaker: str
    text: str

@dataclass(frozen=True)
class ChunkInfo:
    """Данные загруженого пакета"""
    src: int        # ИД источника
    tss: int        # Смещение начала (мс) от начала записи/сессии
    tse: int        # Смещение конца (мс) от начала записи/сессии
    diarize: bool   # Выполнить диаризацию

@dataclass(frozen=True)
class SessionHead:
    """Данные сессии"""
    title: str      # Наименование
    user: str       # Автор
    date_time: str  # Дата-время создания

@dataclass(frozen=True)
class RecognitiontSpeaker:
    spkid: str      # ID спикера <src>/<condidat_id>
    title: str      # Наименование Speaker.title
    ref_spkid: str  # ссылка на spkid
    is_good: bool   # есть как минимум два достаточно длинных сегмента
    is_bad: bool    # все сегменты очень короткие
    
@dataclass(frozen=True)
class RecognitionResult:
    """Сегмент результата STT обработки данных на сервере."""
    tss: int        # Смещение начала (мс) от начала записи/сессии
    tse: int        # Смещение конца (мс) от начала записи/сессии
    spkid: str      # ID спикера <src>/<condidat_id>
    text: str       # Распознанный текст
        
@dataclass(frozen=True)
class Speaker:
    title: str      # наименование
    emb: np.ndarray # эмбеддинг

@dataclass(frozen=False)
class SessionSpeaker:
    emb: np.ndarray        # эмбеддинг    
    spknum: int      = -1  # номер спикера в сессии
    title: str       = ""  # наименование
    count: int       = 0   # количество реплик
    duration: float  = 0.0 # суммарная длительнось в секундах
    max_seg_duration: float  = 0.0 # длительнось в секундах самого длинного сегмента
    good_count: int  = 0   # количество сегментов с хорошей длительностью
    is_good: bool = False  # итоговая оценка хорошести/достоверноси спикера и качества эмбединга
    is_bad: bool = False   # True -> ембеддинг не качественный
    first_ssegi: int = -1  # индекс первого сегмента
    ref_spknum: int  = -1  # для объединения вторичных спикеров с основными
