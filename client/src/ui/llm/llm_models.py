from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------
@dataclass
class Prompt:
    title: str          # название/идентификатор промпта
    is_personal: bool   # это личный промпт
    text: str           # текст промпта
    llmcon: str         # название/идентификатор соединения


@dataclass
class LlmConnection:
    title: str          # название/идентификатор соединения
    is_personal: bool   # это личное соединение
    base_url: str
    api_key: str
    model: str
    system_prompt: str
    temperature: float

# ---------------------------------------------------------------------------
# Работа с префиксом
# ---------------------------------------------------------------------------
_PERSON_PREFIX = "👤"
_GLOBAL_PREFIX = "🌐"

def strip_prefix(title: str) -> str:
    """Убирает префикс для отображения пользователю."""
    if title.startswith(_PERSON_PREFIX) or title.startswith(_GLOBAL_PREFIX):
        return title[1:]
    return title

def apply_prefix(raw_title: str, is_personal: bool) -> str:
    """Добавляет соответствующий префикс"""
    raw = strip_prefix(raw_title)
    if is_personal:
        return f"{_PERSON_PREFIX}{raw}"
    else:
        return f"{_GLOBAL_PREFIX}{raw}"

def is_personal(title: str) -> bool:
    return title.startswith(_PERSON_PREFIX)
