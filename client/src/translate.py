import json
from pathlib import Path
from typing import Dict, List, Optional
import locale

class LanguageManager:
    """Синглтон для управления локализацией."""

    _instance: Optional["LanguageManager"] = None
    _initialized: bool = False

    def __new__(cls) -> "LanguageManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        # Путь к папке с языковыми файлами
        self.directory: Path = Path("./assets/languages")
        # Кэш переводов текущего языка: {код_текста: текст}
        self.translations: Dict[str, str] = {}
        self._initialized = True

    def get_available_languages(self) -> Dict[str, str]:
        """Возвращает словарь доступных языков: {код_языка: наименование}."""
        languages: Dict[str, str] = {}

        if not self.directory.exists():
            return languages

        # Сканируем json файлы в директории
        for file in self.directory.glob("*.json"):
            name_stem: str = file.stem  # Имя файла без расширения
            if "_" in name_stem:
                lang_code, lang_name = name_stem.split("_", 1)
                languages[lang_code] = lang_name

        return languages

    def load_language(self, lang_code: str) -> bool:
        """Загружает выбранный язык в память по его коду."""
        available: Dict[str, str] = self.get_available_languages()

        if lang_code not in available:
            self.translations = {}
            return False

        lang_name: str = available[lang_code]
        file_path: Path = self.directory / f"{lang_code}_{lang_name}.json"

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data: List[Dict[str, str]] = json.load(f)

            # Пересобираем массив объектов в плоский словарь для быстрого поиска
            self.translations = {item["code"]: item["text"] for item in data}
            return True
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            self.translations = {}
            return False

    def translate(self, in_text: str) -> str:
        """Внутренний метод перевода строки."""
        if "|" not in in_text:
            text = self.translations.get(in_text)
            if text: return text            
            return in_text

        # Разделяем по первому символу пайпа
        code, default_text = in_text.split("|", 1)
        # Ищем перевод, иначе возвращаем исходный текст
        return self.translations.get(code, default_text)


def lcvt(text: str) -> str:
    """Глобальная функция локализации."""
    return LanguageManager().translate(text)

def get_os_language():
    try:
        # Инициализируем локаль из настроек операционной системы
        locale.setlocale(locale.LC_ALL, '')
        # Получаем кортеж (язык, кодировка), например ('ru_RU', 'UTF-8')
        lang_tuple = locale.getlocale()
        lang_code = lang_tuple[0]
    except Exception:
        lang_code = None

    # Если стандартный метод вернул None, используем безопасный резервный вариант
    if not lang_code:
        import os
        # Проверяем переменные окружения ОС (Linux/macOS)
        lang_code = os.environ.get('LANG', os.environ.get('LC_ALL', 'en'))

    # Возвращаем первые две буквы (например, 'ru')
    return lang_code[:2].lower() if lang_code else 'en'
