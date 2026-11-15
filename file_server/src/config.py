import os
from dataclasses import dataclass, fields, Field
from typing import Any, Callable
import json
from pathlib import Path
import sys
import platform

@dataclass(frozen=True)
class Config:
    """Класс конфигурации"""

    main_url: str
    token: str

    scan_dir: str # для docker/linux - это каталог в который нужно подкаталогами смонтировать сканируемые каталоги
    scan_dirs_file: str # путь к файлы со списком сканируемых каталгов  

    work_dir: str # путь к рабочему каталогу

    scan_interval: float # интервал сканирования в минутах

    from_env: bool

    def __init__(self) -> None:
        if len(sys.argv) > 1:
            config_path = sys.argv[1] 
            if config_path:
                self._fill_from_file(config_path)
                return
        
        config_path: str = "./config.json"
        if os.path.exists(config_path):
            self._fill_from_file(config_path)
            return

        os_type = platform.system()

        if os_type == "Windows":
            config_path: str = "./config_windows.json"
            if os.path.exists(config_path):
                self._fill_from_file(config_path)
                return
            
        if os_type == "Linux":
            config_path: str = "./config_linux.json"
            if os.path.exists(config_path):
                self._fill_from_file(config_path)
                return

        self._fill_from_env()        
                        
    def _fill_from_file(self, config_file_path: str) ->None:
        print(f"config fill_from_file: {config_file_path}")
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
                self._set_field_value(field, value)
            else:
                self._set_field_empty_value(field)                                     
        
    def _fill_from_env(self) ->None:
        env_param_exists: bool = False

        # Проходим по всем определенным полям датакласса
        for field in fields(self):
            # Ищем переменную в окружении (приводим имя поля к верхнему регистру)
            env_value = os.getenv(field.name.upper())
            if env_value is None:
                env_value = os.getenv(field.name.lower())
            
            if env_value is not None:
                env_param_exists = True

                self._set_field_value(field, env_value)
            else:
                self._set_field_empty_value(field)

        if env_param_exists:
            object.__setattr__(self, "from_env", True)

    def _set_field_value(self, field: Field, value):
        # Приводим тип строки из env к типу, указанному в аннотации поля
        target_type = field.type
        
        # Обработка логического типа (bool)
        if target_type is bool:
            value_to_set = value.lower() in ("true", "1", "yes")
        elif isinstance(target_type, Callable):  # Проверяем, можно ли вызвать тип
            try:
                value_to_set = target_type(value)
            except (ValueError, TypeError):
                value_to_set = value
        else:
            value_to_set = value
        
        # Обходим ограничение frozen=True
        object.__setattr__(self, field.name, value_to_set)        

    def _set_field_empty_value(self, field: Field):
        if field.type is str:
            object.__setattr__(self, field.name, "")
        elif field.type is bool:
            object.__setattr__(self, field.name, False)
        elif field.type is int:
            object.__setattr__(self, field.name, 0)
        elif field.type is float:
            object.__setattr__(self, field.name, 0.0) 

def get_scan_dir_list(config: Config) -> list[str]:
    result: list[str] = []

    if config.scan_dirs_file and Path(config.scan_dirs_file).exists():
        with open(config.scan_dirs_file, "r", encoding="utf-8") as file:
            result = file.read().splitlines()        

    if config.scan_dir:
        result.append(str(config.scan_dir))

    return result