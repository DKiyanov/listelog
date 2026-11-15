from dataclasses import asdict
import keyring

from src.init import *
from src.net.net_client import *
from src.ui.llm.llm_models import *
from src.translate import *

class NetClientLLM:
    def __init__(self, 
        net_client:NetClient,
        local_llm_dir: Path
    ) -> None:
        self.net_client = net_client
        self._client = net_client._client
        self.local_llm_connection_file = local_llm_dir / "personal_connection.json"

# ---------- Prompts ----------

    async def get_prompts(self) -> list[Prompt]:
        """Получает список промптов (глобальные + персональные)."""
        if self._client == None: return []

        response: httpx.Response = await self._client.get("/llm/get-prompts")
        response.raise_for_status()
        return [
            Prompt(
                title=item["title"],
                is_personal=item["is_personal"],
                text=item["text"],
                llmcon=item.get("llmcon", ""),
            )
            for item in response.json()
        ]

    async def save_prompt(
        self,
        title: str,
        text: str,
        llmcon: str,
        is_personal: bool,
    ) -> str:
        """Сохраняет промпт. Возвращает текст ошибки либо пустую строку."""
        if self._client == None: return ""

        title = llm_apply_prefix(title, is_personal)

        payload = {
            "title": title,
            "text": text,
            "llmcon": llmcon,
            "is_personal": is_personal,
        }
        try:
            response = await self._client.post("/llm/save-prompt", json=payload)
        except httpx.HTTPError as e:
            return f"Ошибка сети: {e}"

        if response.status_code != 200:
            return f"Сервер вернул {response.status_code}: {response.text}"

        # Эндпоинт возвращает строку (JSON-encoded)
        try:
            return response.json()
        except ValueError:
            return response.text

    async def del_prompt(self, title: str) -> None:
        """Удаляет промпт по названию."""
        if self._client == None: return

        params = {"title": title, "is_personal": llm_is_personal(title)}
        response: httpx.Response = await self._client.delete("/llm/del-prompt", params=params)
        response.raise_for_status()

    # ---------- Connections ----------

    def _read_local_connections(self) -> list[LlmConnection]:
        result: list[LlmConnection] = []

        if not self.local_llm_connection_file.exists(): return result

        with open(self.local_llm_connection_file, "r", encoding="utf-8") as f:
            data: list[dict[str, Any]] = json.load(f)

        for item in data:
            result.append(LlmConnection(**item))
        
        return result

    def _save_local_connections(self, conections: list[LlmConnection]) -> None:
        data_to_save = [asdict(c) for c in conections]

        self.local_llm_connection_file.parent.mkdir(exist_ok=True)

        with open(self.local_llm_connection_file, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=4)  

    async def get_connections(self) -> list[str]:
        """Получает список названий соединений."""
        local_connections = self._read_local_connections()
        result = [lc.title for lc in local_connections]

        if self._client == None: return result

        response: httpx.Response = await self._client.get("/llm/get-llm-connections")
        response.raise_for_status()
        
        net_connections: list[str] = response.json()
        result.extend(net_connections)

        return result

    async def get_connection(self, title: str) -> LlmConnection | None:
        """Получает соединение по названию."""

        if llm_is_personal(title):
            local_connections = self._read_local_connections()
            connection = next((lc for lc in local_connections if lc.title == title), None)
            if connection:
                connection.api_key = keyring.get_password(f"{SERVICE_NAME}/llmcon/{title}", self.net_client.username) or ""
            return connection

        if self._client == None: return 

        params = {"title": title}
        response: httpx.Response = await self._client.get("/llm/get-llm-connection", params=params)
        response.raise_for_status()
        data = response.json()
        return LlmConnection(**data)

    async def save_connection(
        self,
        title: str,
        base_url: str,
        api_key: str,
        model: str,
        system_prompt: str,
        temperature:float,
        is_personal: bool,
    ) -> None:
        """Сохраняет соединение."""
        title = llm_apply_prefix(title, is_personal)

        if is_personal:
            if not self.net_client.is_login_ok: return

            local_connections = self._read_local_connections()
            local_connections = [lc for lc in local_connections if lc.title != title]
            local_connections.append(LlmConnection(
                title=title,
                base_url=base_url,
                api_key="",
                model=model,
                system_prompt=system_prompt,
                temperature=temperature,
                is_personal=True
            ))
            self._save_local_connections(local_connections)
            keyring.set_password(f"{SERVICE_NAME}/llmcon/{title}", self.net_client.username, api_key)
            return

        if self._client == None: return

        payload = {
            "title": title,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "system_prompt": system_prompt,
            "temperature": temperature,
            "is_personal": is_personal,
        }
        response: httpx.Response = await self._client.post("/llm/save-llm-connection", json=payload)
        response.raise_for_status()

    async def del_connection(self, title: str) -> None:
        """Удаляет соединение по названию."""

        if llm_is_personal(title):
            local_connections = self._read_local_connections()
            local_connections = [lc for lc in local_connections if lc.title != title]
            self._save_local_connections(local_connections)
            return            

        if self._client == None: return

        params = {"title": title}
        response: httpx.Response = await self._client.delete("/llm/del-llm-connection", params=params)
        response.raise_for_status()

    async def _local_llm_processing(self, text: str, prompt: str, connection: LlmConnection) -> tuple[str, str]:
        """
        Отправляет запрос к LLM (OpenAI-совместимый API) с использованием httpx.
        """
        # Формируем заголовки авторизации
        headers = {
            "Authorization": f"Bearer {connection.api_key}",
            "Content-Type": "application/json"
        }

        action_prompt = lcvt("0811|выполни [user-prompt] над данными находящимися в области [content] и верни результат обработки")
        user_prompt = f"\n<content>\n{text}\n</content>\n<user-prompt>\n{prompt}</user-prompt>\n{action_prompt}"

        # Стандартный формат OpenAI-совместимых эндпоинтов
        payload = {
            "model": connection.model,
            "messages": [
                {"role": "system", "content": connection.system_prompt},
                {"role": "user", "content": user_prompt}
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
                return "", f"{lcvt('0812|Ошибка API')} ({e.response.status_code}): {e.response.text}"
            except httpx.RequestError as e:
                # Ошибки сети / таймауты
                return "", f"{lcvt('0813|Ошибка сети при запросе к LLM:')} {e}"


    async def llm_processing(self, text: str, prompt: str, llmcon: str) -> tuple[str, str]:
        if llm_is_personal(llmcon):
            connection = await self.get_connection(llmcon)
            if connection:
                llm_result, error = await self._local_llm_processing(text, prompt, connection)
                return llm_result, error
            return "", lcvt("0814|соедиение не найдено")
            
        if self._client == None: return "", ""

        try:
            params = {"text": text, "prompt": prompt, "llmcon": llmcon}
            response: httpx.Response = await self._client.get("/llm/llm-processing", params=params)        
            response.raise_for_status()
            data = response.json()
            llm_result = data["llm_result"]
            error = data["error"]

            return llm_result, error
        except httpx.HTTPStatusError as e:
            # Здесь можно обработать специфичные ошибки API (например, неверный ключ)
            return "", f"{lcvt('0815|Ошибка API')} ({e.response.status_code}): {e.response.text}"
        except httpx.RequestError as e:
            # Ошибки сети / таймауты
            return "", f"{lcvt('0816|Ошибка сети при запросе к LLM:')} {e}"
