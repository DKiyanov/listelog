import os
from dataclasses import dataclass, fields
from typing import Any, Callable
import json
from pathlib import Path
import sys
import platform

@dataclass(frozen=True)
class Config:
    """Класс конфигурации"""
    port: int
 
    ldap_domain: str
    ldap_server: str

    tokens_file_path: str
    token_lifetime_hours: float

    verbose: bool

    audio_data_dir: str
    sessions_dir: str
    users_dir: str
    site_dir:str

    demo_login: str

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

        env_value = os.getenv("AUDIO_DATA_DIR")
        if env_value is None:
            env_value = os.getenv("audio_data_dir")

        if env_value is not None:
            self._fill_from_env()
            return            
                        
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
        print(f"config fill_from_env")
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

