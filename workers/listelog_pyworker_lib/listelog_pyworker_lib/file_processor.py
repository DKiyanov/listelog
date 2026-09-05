from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from typing import Any,  Callable
import json
from pathlib import Path
import os
import sys
import re
from datetime import datetime, timedelta
import math
from dataclasses import asdict
from typing import List, Any
from pathlib import Path
import json

from .models import *

@dataclass
class Task:    
    in_proc_dir: Path
    work_type: str = ""
    sid: str = ""
    cid: str = ""
    next_work_type: str = ""
    original_file: str = ""
    session_dir: Path | None = None
    in_proc_file: Path | None = None 
    last_time: datetime | None = None

    def start_processing(self, max_proc_duration_sec: float):
        # Должно быть вызвано в самом начале обработки

        # прибавить к текущему времени  duration: int в секундах 
        # округлить до минут в большую сторону
        # сформировать строку в формате ГГММДДччмм       
        future_time = datetime.now() + timedelta(seconds=max_proc_duration_sec)
        rounded_timestamp = math.ceil(future_time.timestamp() / 60) * 60
        self.last_time = datetime.fromtimestamp(rounded_timestamp)
        last_time_str = self.last_time.strftime("%y%m%d%H%M")

        original_file = Path(self.original_file)

        self.in_proc_file = self.in_proc_dir / f"{self.sid}_{self.cid}_{self.work_type}_{self.next_work_type}_{last_time_str}{original_file.suffix}"
        original_file.rename(self.in_proc_file)   

    def save_sttd_result(self, result: List[RecognitionResult], spks: List[RecognitiontSpeaker]) -> None:
            assert self.session_dir is not None

            session_result_dir = self.session_dir / "result"
            session_result_dir.mkdir(exist_ok=True)
            
            chunk_result_path = session_result_dir / f"{self.cid}_result.json"
            result_path = self.session_dir / "result.json"

            spks_dict = [asdict(item) for item in spks]
            results_dict = [asdict(item) for item in result]

            result_dict_ok: dict[str, Any] = {
                "spks" : spks_dict,
                "results"  : results_dict
            }   

            # Записываем в файл результата
            with open(chunk_result_path, 'w', encoding='utf-8') as f:
                json.dump(result_dict_ok, f, ensure_ascii=False, indent=4)

            for item in results_dict:
                item["cid"] = self.cid

            # Добавляем в файл результатов
            dump_result = ",\n".join(json.dumps(item, ensure_ascii=False, indent=4) for item in results_dict)             

            if result_path.exists():
                dump_result = ",\n" + dump_result

            with open(result_path, 'a', encoding='utf-8') as f:
                f.write(dump_result)           

class FileProcessor(ABC):

    @abstractmethod
    def __init__(self, main_config: 'MainConfig', config_path: str) -> None:
        pass
    # читает настройки
    # класс _Config определяем в том же модуле

    @abstractmethod
    def get_work_types(self) -> str:
        pass
    # возвращает строку с поддерживаемыми типами работ (work_types) через ",", пробелы допустимы
    # воркер передаёт эту строку в сообщении ready поле work_types

    @abstractmethod
    async def process_file(self, task: Task) -> str:
        pass
    # выполняет обработку
    # сохраняет результаты в каталоге сессии в соотв. файлах
    # возвращает имя/идентификатор следущего воркера либо пустую строку
    # перед началом обработки нужно оценить максимальную возможну длительность обработки в секундах и вызвать start_processing
    # def start_processing(self, max_proc_duration_sec: int) -> Path: возвращает рабочее имя file_path

@dataclass(frozen=True)
class AbstractConfig(ABC):
    """Класс конфигурации"""

    def __init__(self, config_path: str) -> None:
        if config_path == 'env':
            self._fill_from_env()
        else:
            self._fill_from_file(config_path)                        

    def _fill_from_file(self, config_file_path: str) ->None:
        path: Path = Path(config_file_path)

        with open(path, "r", encoding="utf-8") as file:
            raw_data: dict[str, Any] = json.load(file)

        # Преобразуем ключи из 'k-v' в 'k_v' для соответствия атрибутам класса
        sanitized_data: dict[str, Any] = {
            key.replace("-", "_"): value for key, value in raw_data.items()
        }

        for field in fields(self):
            value = sanitized_data.get(field.name)
            if value:
                object.__setattr__(self, field.name, value)
            else:
                if field.type is str:
                    object.__setattr__(self, field.name, "")
                elif field.type is bool:
                    object.__setattr__(self, field.name, False)
                elif field.type is int:
                    object.__setattr__(self, field.name, 0)
                elif field.type is float:
                    object.__setattr__(self, field.name, 0.0)                                        
        
    def _fill_from_env(self) ->None:
        # Проходим по всем определенным полям датакласса
        for field in fields(self):
            # Ищем переменную в окружении (приводим имя поля к верхнему регистру)
            env_value = os.getenv(field.name.upper())
            if env_value is None:
                env_value = os.getenv(field.name.lower())
            
            if env_value is not None:
                # Приводим тип строки из env к типу, указанному в аннотации поля
                target_type = field.type
                
                # Обработка логического типа (bool)
                if target_type is bool:
                    value_to_set = env_value.lower() in ("true", "1", "yes")
                elif isinstance(target_type, Callable):  # Проверяем, можно ли вызвать тип
                    try:
                        value_to_set = target_type(env_value)
                    except (ValueError, TypeError):
                        value_to_set = env_value
                else:
                    value_to_set = env_value
                
                # Обходим ограничение frozen=True
                object.__setattr__(self, field.name, value_to_set)
            else:
                if field.type is str:
                    object.__setattr__(self, field.name, "")
                elif field.type is bool:
                    object.__setattr__(self, field.name, False)
                elif field.type is int:
                    object.__setattr__(self, field.name, 0)
                elif field.type is float:
                    object.__setattr__(self, field.name, 0.0)                   

def _name_convert(name: str) -> str:
    # Добавляет подчёркивание перед заглавной буквой, если перед ней идёт строчная
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    # Добавляет подчёркивание между строчной (или цифрой) и заглавной буквой
    s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1)
    # Переводит весь результат в нижний регистр
    return s2.lower()        

def get_class_config_path(cls: Any) -> str:
    # Находим модуль, в котором определен класс
    module = sys.modules[cls.__module__]

    if not module.__file__:
        return ""

    file_name = f"{_name_convert(cls.__name__)}.json"

    # Получаем директорию модуля и добавляем имя файла класса
    config_path = Path(module.__file__).parent / file_name
    if not config_path.exists():
        config_path = Path(f"./{file_name}")

    # Возвращаем строковое представление абсолютного пути
    return str(config_path.resolve())

@dataclass(frozen=True)
class MainConfig(AbstractConfig):
    audio_data_dir: str
    sessions_dir: str
    users_dir: str    
    
    def __init__(self, config_path: str) -> None:
        super().__init__(config_path)    

    