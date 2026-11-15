from dataclasses import dataclass
from typing import Callable, List
import numpy as np

@dataclass(frozen=True)
class RawChunk:
    source_id: int  # Идентификатор источника
    mtime: int   # время time.monotonic()
    raw_data: np.ndarray  # PCM-данные в формате float32 numpy array

@dataclass(frozen=True)
class ExtChunk:
    chunk: np.ndarray
    vadr: float # Результат VAD

@dataclass(frozen=True)
class Fragment:
    source_id: int  # Идентификатор источника
    ts_start: int   # время в миллисекундах от начала записи
    ts_end: int     # время конца в миллисекундах от начала записи
    chunks: list[ExtChunk] 

@dataclass(frozen=True)
class EncodedFragment:
    """Подготовленный к сохранению/отправке блок"""
    source_id: int  # Идентификатор источника
    ts_start: int   # время в миллисекундах от начала записи
    ts_end: int     # время конца в миллисекундах от начала записи
    is_bad: bool    # очень плохое соотношение голос/шум - сохранить но не транскрибировать
    encoded_data: bytes  # OGG данные

@dataclass(frozen=False)
class ResultSegment: # см. worker.RecognitionResult - почти совпадает
    """Сегмент результата STT обработки данных на сервере."""
    cid: int        # Идентификатор пакета
    tss: int        # Смещение начала (мс) от начала записи/сессии
    tse: int        # Смещение конца (мс) от начала записи/сессии
    spkid: str      # ID спикера <source_id>/<spiker_num>
    text: str       # Распознанный текст

@dataclass
class RecordSourceInfo:
    """Данные записываемого источника"""
    source_id: int  # Идентификатор источника
    diarize: bool   # Необходима диаризация

@dataclass(frozen=True)
class SttSession:
    sid: str      # ИД сессии
    title: str    # Наименование сессии
    date: str     # Дата_время формат YYYYMMDDHHmm 
    duration: int # Длительность в минтах    

@dataclass(frozen=True)
class SttSessionStatus:
    sid: str        # ИД сессии
    is_closed: bool # сессия заккрыта
    percent: int    # процент обработки

@dataclass
class Speaker: # совпадает по структуре с worker.RecognitiontSpeaker
    spkid: str      # ID спикера <source_id>/<spiker_num>
    title: str      # Наименование спикера
    ref_spkid: str  # ссылка на spkid
    is_good: bool   # есть как минимум два достаточно длинных сегмента
    is_bad: bool    # все сегменты очень короткие    

OnRawFragment = Callable[[RawChunk], None]
OnFragment = Callable[[Fragment], None]
SimpleCallBack = Callable[[], None]
OnResult = Callable[[List[ResultSegment], List[Speaker]], None]
OnSelectSttSession = Callable[[SttSession], None]