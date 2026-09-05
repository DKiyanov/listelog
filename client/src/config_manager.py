import json
import getpass
from pathlib import Path
from typing import Any, Dict, Optional

from src.models import *

class ConfigManager:
    """Класс для управления конфигурационным файлом приложения."""
    # если есть файл main_config.json в корне - берём настройки из него - какие есть, 
    # остальные должен ввести пользователь, а те что есть - показываем но блокируем ввод
    # настройки введённые пользователем - храним в профиле пользователя
    # как создавать этот main_config.json - опишем в RedMe.md к программе
    # в main_config.json может быть указан token - если указан - используем его
    
    def __init__(self) -> None:
        self._config_dir = Path.home() / f".{SERVICE_NAME}"
        self._config_path: Path = self._config_dir/ "config.json" 

        self._main_config_path: Path = Path("./main_config.json")

        self.config_loaded: bool = False
        self.config_ok: bool = False

        self.base_url: str = ""
        self.login: str = ""   
        self.token: str = ""

        self.base_url_lock: bool = False
        self.login_lock: bool = False
        self.password_lock: bool = False

        self.theme: str = ""
        self.language: str = ""


    def load(self) -> None:
        try:
            if self._main_config_path.exists():
                try:
                    with open(self._main_config_path, "r", encoding="utf-8") as f:
                        data: Any = json.load(f)
                        if isinstance(data, dict):
                            jsd = {str(k): str(v) for k, v in data.items()}
                            self.base_url = jsd.get("base_url", "")
                            self.login = jsd.get("login", "")    
                            self.token = jsd.get("token", "")

                            if self.base_url != "": self.base_url_lock = True
                            if self.login != "": self.login_lock = True
                            if self.token != "": self.password_lock = True

                except (json.JSONDecodeError, OSError) as e: 
                    print(f"[ConfigManager] Ошибка чтения файла: {e}")

            if not self._config_path.exists():
                return

            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    data: Any = json.load(f)
                    if isinstance(data, dict):
                        jsd = {str(k): str(v) for k, v in data.items()}
                        if not self.base_url_lock:
                            self.base_url = jsd.get("base_url", "")
                        if not self.login_lock:
                            self.login = jsd.get("login", "")
                            
                        self.theme    = jsd.get("theme", "")
                        self.language = jsd.get("language", "")
                
                self.config_loaded = True

            except (json.JSONDecodeError, OSError) as e:
                print(f"[ConfigManager] Ошибка чтения файла: {e}")
            return
        finally:
            self.config_ok = self.base_url != "" and self.login != ""

    def save(self, base_url: str, username: str, theme: str, language: str) -> None:
        """Сохраняет настройки в конфигурационный файл."""
        
        data: Dict[str, str] = {
            "base_url": base_url,
            "login": username,
            "theme": theme,
            "language": language
        }
        try:
            if not self._config_dir .exists():
                self._config_dir.mkdir(parents=True, exist_ok=True)

            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

            self.base_url = base_url
            self.login = username
            self.config_ok = self.base_url != "" and self.login != ""
            self.theme = theme
            self.language = language
        except OSError as e:
            print(f"[ConfigManager] Ошибка записи файла: {e}")
        

    @staticmethod
    def get_system_username() -> str:
        """Возвращает имя текущего пользователя операционной системы."""
        return getpass.getuser()
