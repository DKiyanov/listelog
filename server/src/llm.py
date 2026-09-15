import json
import httpx
from pathlib import Path
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from dataclasses import dataclass

from src.init import *

router = APIRouter(
    prefix="/llm",
    tags=["LLM"]
)

@dataclass
class Prompt:
    title: str
    is_personal: bool
    text: str
    llmcon: str

@dataclass
class LlmConnection:
    title: str
    is_personal: bool
    base_url: str
    api_key: str
    model: str
    system_prompt: str
    temperature:float

# ---------- Pydantic-схемы для тела запросов ----------

class SavePromptRequest(BaseModel):
    title: str
    text: str
    llmcon: str
    is_personal: bool


class SaveConnectionRequest(BaseModel):
    title: str
    base_url: str
    api_key: str
    model: str
    system_prompt: str
    temperature:float
    is_personal: bool


# ---------- Вспомогательные функции ----------

def _global_prompts_file() -> Path:
    return Path(config.llm_dir) / "global_prompts.json"

def _global_connection_file() -> Path:
    return Path(config.llm_dir) / "global_connection.json"

def _user_prompts_file(user: str) -> Path:
    return Path(config.users_dir) / user / "llm" / "personal_prompts.json"

def _load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- Prompts ----------

@router.get("/get-prompts", response_model=List[Prompt], status_code=status.HTTP_200_OK)
async def get_prompts(user: str = Depends(verify_token)) -> List[Prompt]:
    """Возвращает объединённый список глобальных и персональных промптов."""
    result: List[Prompt] = []

    for item in _load_json_list(_global_prompts_file()):
        result.append(Prompt(
            title=item["title"],
            is_personal=False,
            text=item["text"],
            llmcon=item.get("llmcon", ""),
        ))

    for item in _load_json_list(_user_prompts_file(user)):
        result.append(Prompt(
            title=item["title"],
            is_personal=True,
            text=item["text"],
            llmcon=item.get("llmcon", ""),
        ))

    return result


@router.post("/save-prompt", response_model=str, status_code=status.HTTP_200_OK)
async def save_prompt(
    payload: SavePromptRequest,
    user: str = Depends(verify_token),
) -> str:
    """Сохраняет промпт. Возвращает текст ошибки либо пустую строку."""
    if not payload.title.strip():
        return "Пустое имя промпта"
    if not payload.text.strip():
        return "Пустой текст промпта"

    path = _user_prompts_file(user) if payload.is_personal else _global_prompts_file()
    items = _load_json_list(path)

    new_item = {
        "title": payload.title,
        "text": payload.text,
        "llmcon": payload.llmcon,
    }

    for i, item in enumerate(items):
        if item.get("title") == payload.title:
            items[i] = new_item
            break
    else:
        items.append(new_item)

    try:
        _save_json(path, items)
    except OSError as e:
        return f"Ошибка сохранения: {e}"

    return ""


@router.delete("/del-prompt", status_code=status.HTTP_200_OK)
async def del_prompt(
    title: str,
    is_personal: bool = False,
    user: str = Depends(verify_token),
):
    """Удаляет промпт по названию."""
    path = _user_prompts_file(user) if is_personal else _global_prompts_file()
    items = _load_json_list(path)
    filtered = [item for item in items if item.get("title") != title]

    if len(filtered) == len(items):
        raise HTTPException(status_code=404, detail="Промпт не найден")

    _save_json(path, filtered)
    return {"status": "ok"}


# ---------- Connections ----------

@router.get("/get-llm-connections", response_model=List[str], status_code=status.HTTP_200_OK)
async def get_llm_connections(
    user: str = Depends(verify_token),
) -> List[str]:
    """Получает список соединений"""

    global_connection_file = _global_connection_file()

    titles: List[str] = []

    for item in _load_json_list(global_connection_file):
        title = item.get("title")
        titles.append(title)

    return titles


@router.get("/get-llm-connection", response_model=LlmConnection, status_code=status.HTTP_200_OK)
async def get_llm_connection(
    title: str,
    user: str = Depends(verify_token),
) -> LlmConnection:
    """Возвращает соединение по названию."""

    for item in _load_json_list(_global_connection_file()):
        if item.get("title") == title:
            return LlmConnection(
                title=item["title"],
                is_personal=False,
                base_url=item.get("base_url", ""),
                api_key=item.get("api_key", ""),
                model=item.get("model", ""),
                system_prompt=item.get("system_prompt", ""),
                temperature=item.get("temperature", 0.7),
            )

    raise HTTPException(status_code=404, detail="Соединение не найдено")


@router.post("/save-llm-connection", status_code=status.HTTP_200_OK)
async def save_llm_connection(
    payload: SaveConnectionRequest,
    user: str = Depends(verify_token),
):
    """Сохраняет соединение."""
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="Пустое имя соединения")

    path = _global_connection_file()
    items = _load_json_list(path)

    new_item = {
        "title": payload.title,
        "base_url": payload.base_url,
        "api_key": payload.api_key,
        "model": payload.model,
        "system_prompt": payload.system_prompt,
        "temperature": payload.temperature,
    }

    for i, item in enumerate(items):
        if item.get("title") == payload.title:
            items[i] = new_item
            break
    else:
        items.append(new_item)

    _save_json(path, items)
    return {"status": "ok"}

@router.delete("/del-llm-connection", status_code=status.HTTP_200_OK)
async def del_llm_connection(
    title: str,
    user: str = Depends(verify_token),
):
    """Удаляет соединение по названию."""
    path = _global_connection_file()
    items = _load_json_list(path)
    filtered = [item for item in items if item.get("title") != title]

    if len(filtered) == len(items):
        raise HTTPException(status_code=404, detail="Соединение не найдено")

    _save_json(path, filtered)
    return {"status": "ok"}

@router.get("/llm-processing", response_model=LlmConnection, status_code=status.HTTP_200_OK)
async def llm_processing(
    text: str,
    prompt: str, 
    llmcon: str,
    user: str = Depends(verify_token),
) -> tuple[str, str]:
    """Возвращает результат обработки в LLM"""

    path = _global_connection_file()
    connections = _load_json_list(path)
    connection = next((lc for lc in connections if lc.title == llmcon), None)
    if not connection:
        return "", f"соединение с LLM {llmcon} - не найдено"

    # Формируем заголовки авторизации
    headers = {
        "Authorization": f"Bearer {connection.api_key}",
        "Content-Type": "application/json"
    }
    
    # Стандартный формат OpenAI-совместимых эндпоинтов
    action_prompt = "выполни [user-prompt] над данными находящимися в области [content] и верни результат обработки"
    payload = {
        "model": connection.model,
        "messages": [
            {"role": "system", "content": connection.system_prompt},
            {"role": "user", "content": f"\n<content>\n{text}\n</content>\n<user-prompt>\n{prompt}</user-prompt>\n{action_prompt}"}
        ],
        "temperature": connection.temperature
    }
    
    # Формируем корректный URL (убедимся, что он смотрит на эндпоинт v1/chat/completions)
    url = connection.base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers, timeout=60.0)
            response.raise_for_status()  # Вызовет ошибку при HTTP-статусах 4xx/5xx
            
            data = response.json()
            llm_result = data["choices"][0]["message"]["content"]
            return llm_result, ""                
        except httpx.HTTPStatusError as e:
            # Здесь можно обработать специфичные ошибки API (например, неверный ключ)
            return "", f"Ошибка API ({e.response.status_code}): {e.response.text}"
        except httpx.RequestError as e:
            # Ошибки сети / таймауты
            return "", f"Ошибка сети при запросе к LLM: {e}"    