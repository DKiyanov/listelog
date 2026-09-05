from dataclasses import dataclass, field
from typing import List
from fastapi import WebSocket

@dataclass(frozen=True)
class SttSession:
    sid: str      # ИД сессии
    title: str    # Наименование сессии
    date: str     # Дата_время формат YYYYMMDDHHmm 
    duration: int # Длительность в минтах

@dataclass(frozen=True)
class SttSegment:
    cid: int        # Ид блока контента
    tss: int        # Смещение начала (мс) от начала записи/сессии
    tse: int        # Смещение конца (мс) от начала записи/сессии
    spkid: str      # ID спикера <source_id>/<spiker_num>
    text: str       # Распознанный текст

@dataclass(frozen=True)
class SttSpeaker:
    spkid: str      # ID спикера <source_id>/<spiker_num>
    title: str      # Наименование спикера
    ref_spkid: str  # ссылка на spkid
    is_good: bool   # есть как минимум два достаточно длинных сегмента
    is_bad: bool    # все сегменты очень короткие    

@dataclass(frozen=True)
class Task:
    work_type: str  # тип обработки
    sid: str        # ИД сессии
    cid: str        # ИД файла
    file: str       # имя файла 
    next: str       # следующй обработчик

@dataclass
class Worker:
    ws: WebSocket
    work_types: list[str] = field(default_factory=list)  # Типы работ поддерживаемые воркером
    task_key: str | None = None

def get_task_key(sid: str, cid: str, work_type: str) -> str:
    return f"{sid}_{cid}_{work_type}"    