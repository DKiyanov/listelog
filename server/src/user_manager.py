import hashlib
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel
from typing import Dict
from datetime import datetime, timezone

from src.config import Config

class UserToken(BaseModel):
    login: str
    token_hash: str
    salt: str
    created_at: str  # Формат: ISO 8601 (YYYY-MM-DDTHH:MM:SS)
    expires_at: str  # Формат: ISO 8601 (YYYY-MM-DDTHH:MM:SS)
    is_active: bool

class UserManager:
    def __init__(
        self, 
        config: Config
    ) -> None:
        self.config = config

        self.tokens_file_path: Path = Path(config.tokens_file_path)
        self._tokens: List[UserToken] = []
        self._load_tokens()

    def _load_tokens(self) -> None:
        """Считывает токены из файла при старте сервера."""
        if not self.tokens_file_path.exists():
            self._tokens = []
            return
        
        try:
            with open(self.tokens_file_path, "r", encoding="utf-8") as f:
                data: list = json.load(f)
                self._tokens = [UserToken(**item) for item in data]
        except (json.JSONDecodeError, ValueError):
            # Если файл поврежден, стартуем с пустым списком для безопасности
            self._tokens = []

    def _save_tokens(self) -> None:
        """Сохраняет текущее состояние токенов в JSON файл."""
        with open(self.tokens_file_path, "w", encoding="utf-8") as f:
            # Преобразуем pydantic-модели в dict для сериализации
            json.dump([token.model_dump() for token in self._tokens], f, ensure_ascii=False, indent=4)

    @staticmethod
    def _generate_hash(token: str, salt: str, created_at: str, expires_at: str) -> str:
        """Создает безопасный SHA-256 хеш строки (токен + соль + создание + завершение)."""
        payload: str = f"{token}{salt}{created_at}{expires_at}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_token_expired(expires_at_str: str) -> bool:
        """Проверяет, истек ли срок действия токена."""
        try:
            expires_at: datetime = datetime.fromisoformat(expires_at_str)
            return datetime.now() > expires_at
        except ValueError:
            return True

    def generate_user_token(self, login: str, end_date: Optional[str] = None) -> str:
        """
        Генерирует новый токен для пользователя.
        end_date: строка в формате YYYYMMDD. Если не передана, берется из Config.token_lifetime.
        """
        # Спецификация: случайная строка 50 символов (латинские буквы и цифры)
        alphabet: str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        raw_token: str = "".join(secrets.choice(alphabet) for _ in range(50))
        
        # Расчет дат
        now: datetime = datetime.now()
        created_at_str: str = now.isoformat()
        
        if end_date:
            try:
                # Преобразование из формата YYYYMMDD в конец указанного дня (23:59:59)
                parsed_date: datetime = datetime.strptime(end_date, "%Y%m%d")
                expires_at: datetime = parsed_date.replace(hour=23, minute=59, second=59)
            except ValueError:
                raise ValueError("Неверный формат end_date. Ожидается YYYYMMDD.")
        else:
            expires_at = now + timedelta(hours=self.config.token_lifetime_hours) 
            
        expires_at_str: str = expires_at.isoformat()
        
        # Генерация соли и хеша
        salt: str = secrets.token_hex(16)
        token_hash: str = self._generate_hash(raw_token, salt, created_at_str, expires_at_str)
        
        # Создание объекта токена
        new_token_entry = UserToken(
            login=login,
            token_hash=token_hash,
            salt=salt,
            created_at=created_at_str,
            expires_at=expires_at_str,
            is_active=True
        )
        
        # Сохранение в память и обновление файла
        self._tokens.append(new_token_entry)
        self._save_tokens()

        self.user_log_add(login, "ADD_TOKEN", "-")

        return raw_token

    def user_log_add(self, login: str, action: str, data: str, title: str = ""):
        """Добавляет запись в лог пользователя"""
        log_data: Dict[str, str] = {
            "date_time": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "data": data,
            "title": title,
        }

        json_str = json.dumps(log_data, ensure_ascii=False, indent=4) + "\n,"

        user_dir = Path(self.config.users_dir,  login)
        user_dir.mkdir(exist_ok=True)

        log_path = Path(self.config.users_dir, login, "log.json")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json_str)

    def get_user(self, token: str) -> Optional[str]:
        """Возвращает login пользователя по его "сырому" токену, если он валиден."""
        for entry in self._tokens:
            if not entry.is_active:
                continue
                
            if self._is_token_expired(entry.expires_at):
                continue
            
            # Проверяем, совпадает ли присланный токен с сохраненным хешем
            calculated_hash: str = self._generate_hash(
                token=token,
                salt=entry.salt,
                created_at=entry.created_at,
                expires_at=entry.expires_at
            )
            
            if secrets.compare_digest(entry.token_hash, calculated_hash):
                return entry.login
                
        return None

# if __name__ == "__main__":
#     _config = Config()
#     _user_manager = UserManager(_config)
#     _new_token = _user_manager.generate_user_token("TEST", "20261231")
#     print(f"токен сгенерирован: {_new_token}")
