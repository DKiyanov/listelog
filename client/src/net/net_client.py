import asyncio
from dataclasses import asdict
from typing import List, Optional, Dict, Any, Tuple
import httpx
import keyring
import keyring.errors
import asyncio
import io

from src.models import *
from src.config_manager import *
from src.ui.llm.llm_models import *
from src.translate import *

class NetClient:
    """Сетевой клиент для взаимодействия с сервером STT."""

    def __init__(
            self,
            on_send: SimpleCallBack,
            on_results: OnResult,
            local_llm_dir: Path
    ) -> None:

        self.on_send: SimpleCallBack = on_send
        self.on_results: OnResult = on_results        

        self.is_login_ok = False
        self._client: Optional[httpx.AsyncClient] = None
        self.base_url: str = ""
        self.username: str = ""
        self.token: str = ""

        self.local_llm_connection_file = local_llm_dir / "personal_connection.json"

        self.current_sid: Optional[str] = None
        self.sid_is_open: bool = False

        self.sended_count: int = 0 # Количество отправленых пакетов в рамках сессии
        self.sended_ms: int = 0 # Сколько отправлено на сервер за сессию
        
        self.result_count: int = 0 # Количество полученых результатов за сессию
        self.result_ms: int = 0 # Сколько получено результата 

        self.cids: Dict[int, int] = {} # cid -> длительность звука мс
        
        self.last_error: str = ""


    def _set_login_ok(self, base_url: str, username: str, token: str):
        self.is_login_ok = True
        self.base_url = base_url
        self.token = token
        self.username = username

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"}
        )

    def _logout(self):
        self.is_login_ok = False
        self.token = ""
        self._client = None

    def login(self, config: ConfigManager) -> bool:
        return self.login_ex(config.base_url, config.login, config.token)

    def login_ex(self, base_url: str, username: str, token: Optional[str]) -> bool:
        """Выполняет автоматический вход по сохраненным данным."""
        self.last_error = ""

        try:
            # Чтение токена из keyring
            if token is None or token == "":
                token = keyring.get_password(SERVICE_NAME, username)

                if not token:
                    self.last_error = f"{lcvt('0801|Токен для пользователя')} '{username}' {lcvt('0802|не найден в локальном хранилище.')}"
                    return False

            url = f"{base_url.rstrip('/')}/check-token"
            headers = {"Authorization": f"Bearer {token}"}

            # Отправка POST-запроса для проверки токена
            response = httpx.post(url, headers=headers)
            response.raise_for_status()

            self._set_login_ok(base_url, username, token)

            return True

        except keyring.errors.KeyringError as e:
            self.last_error = f"{lcvt('0803|Ошибка чтения из системного хранилища (keyring):')} {str(e)}"
            return False
        except httpx.HTTPStatusError as e:
            self.last_error = f"{lcvt('0804|Ошибка проверки токена')} ({e.response.status_code}): {e.response.text}"
            return False
        except httpx.RequestError as e:
            self.last_error = f"{lcvt('0805|Ошибка сети при проверке токена:')} {str(e)}"
            return False

    def logup(self, base_url: str, username: str, password: str) -> bool:
        """Выполняет первичную авторизацию и сохраняет ключ в secure-store."""
        self.last_error = ""

        url = f"{base_url.rstrip('/')}/get-token"
        auth = (username, password)

        try:
            # Отправка POST-запроса с Basic Auth
            response = httpx.post(url, auth=auth)
            response.raise_for_status()

            # Извлечение токена из ответа
            data = response.json()
            token = data.get("access_token")
            token_type = data.get("token_type")

            if not token or not token_type or token_type != "bearer":
                return False

            # Сохранение токена в системное хранилище
            keyring.set_password(SERVICE_NAME, username, token)

            self._set_login_ok(base_url, username, token)

            return True

        except httpx.HTTPStatusError as e:
            self.last_error = f"{lcvt('0806|Ошибка сервера')} ({e.response.status_code}): {e.response.text}"
            return False
        except httpx.RequestError as e:
            self.last_error = f"{lcvt('0807|Ошибка сети при запросе:')} {str(e)}"
            return False
        except ValueError:
            self.last_error = lcvt("0808|Сервер вернул некорректный JSON.")
            return False
        except keyring.errors.KeyringError as e:
            self.last_error = f"{lcvt('0809|Ошибка системного хранилища (keyring):')} {str(e)}"
            return False

    async def start_recording(self, title: str) -> None:
        """Открывает новую сессию записи на сервере."""
        if self._client == None: return

        url: str = "/open-session"
        payload: Dict[str, str] = {"title": title}

        response: httpx.Response = await self._client.post(url, json=payload)
        response.raise_for_status()

        data: Dict[str, Any] = response.json()
        self.current_sid = str(data["sid"])
        self.sid_is_open = True

        self.sended_count = 0
        self.sended_ms = 0
        self.cids.clear()
        self.result_count = 0
        self.result_ms = 0

        await self._start_get_results()

    async def send_packet(self, packet: EncodedChunk, cid: int, diarize: bool) -> None:
        """Передает данные блока звукового потока на сервер."""
        if self._client == None: return

        if not self.current_sid:
            raise RuntimeError(lcvt("0810|Сессия не открыта. Сначала вызовите start_recording."))

        url: str = "/upload-chunk"
        params: Dict[str, Any] = {
            "sid": self.current_sid,
            "cid": cid,
            "src": packet.source_id,
            "tss": packet.ts_start,
            "tse": packet.ts_end,
            "diarize": str(diarize).lower()  # Приведение к валидному строковому bool для URL
        }
        headers: Dict[str, str] = {"Content-Type": "application/octet-stream"}    

        response: httpx.Response = await self._client.post(
            url,
            params=params,
            content=packet.encoded_data,
            headers=headers
        )
        response.raise_for_status()

        if not cid in self.cids:
            self.sended_count += 1
            size_ms = packet.ts_end - packet.ts_start
            self.sended_ms += size_ms
            self.cids[cid] = size_ms

        self.on_send()

    async def get_proced_audio(self, cid: int) -> io.BytesIO|None:
        """Возвращает сохранённые аудио-данные"""
        if self._client is None: return
        if not self.current_sid: return

        url: str = "/get-proced-audio"
        params: Dict[str, str] = {
            "sid": self.current_sid,
            "cid": str(cid)
        }

        response: httpx.Response = await self._client.get(url, params=params)
        response.raise_for_status() 

        audio_buffer = io.BytesIO(response.content)
        return audio_buffer

    async def reset_audio(self, cid: int) -> None:
        """устанавливает фрагмент на повторную обработку"""
        if self._client is None: return
        if not self.current_sid: return

        url: str = "/reset-audio"
        params: Dict[str, str] = {
            "sid": self.current_sid,
            "cid": str(cid)
        }

        response: httpx.Response = await self._client.post(url, params=params)
        response.raise_for_status() 

    async def stop_recording(self) -> None:
        """Закрывает текущую сессию записи."""
        if self._client is None: return
        if not self.current_sid: return

        url: str = "/close-session"
        params: Dict[str, str] = {"sid": self.current_sid}

        response: httpx.Response = await self._client.post(url, params=params)
        response.raise_for_status()

        self.sid_is_open = False

    async def delete_session(self) -> None:
        """Удаляет все данные последней сессии на сервере."""
        if self._client == None: return
        if not self.current_sid: return

        url: str = "/delete-session"
        params: Dict[str, str] = {"sid": self.current_sid}

        response: httpx.Response = await self._client.post(url, params=params)
        response.raise_for_status()
        self.current_sid = None

    async def get_sessions(self) -> List[SttSession]:
        """Получает список сессий пользователя"""
        result: List[SttSession] = []

        if self._client == None: return []

        url: str = "/get-stt-sessions"

        response: httpx.Response = await self._client.get(url)
        response.raise_for_status()

        stts_list: List[Dict[str, Any]] = response.json()

        for stts in stts_list:
            result.append(SttSession(
                sid      = str(stts["sid"]),
                title    = str(stts["title"]),
                date     = str(stts["date"]),
                duration = int(stts["duration"]),
            ))
        
        return result

    async def get_session_data(self, sid: str) -> Tuple[List[ResultSegment], List[Speaker]] :
        """Получает список сессий пользователя"""
        if self._client == None: return [], []

        url: str = "/get-stt-ses-data"

        params: Dict[str, str] = {"sid": sid}

        response: httpx.Response = await self._client.get(url, params=params)
        response.raise_for_status()
        response_dict: Dict[str, Any] = response.json()

        return self._prepare_data(response_dict)


    def _prepare_data(self, data: Dict[str, Any], in_cid: int | None = None ) -> Tuple[List[ResultSegment], List[Speaker]]:
        result: List[ResultSegment] = []
        spks: List[Speaker] = []

        stts_list: List[Dict[str, Any]] = data["results"]
        spk_list: List[Dict[str, Any]] = data["spks"]

        for stts in stts_list:
            cid = in_cid if in_cid is not None else int(stts["cid"])
            result.append(ResultSegment(
                cid   = cid,
                tss   = int(stts["tss"]),
                tse   = int(stts["tse"]),
                spkid = str(stts["spkid"]),
                text  = str(stts["text"])
            ))

        for spk in spk_list:
            spks.append(Speaker(
                spkid     = str(spk["spkid"]),
                title     = str(spk["title"]),
                ref_spkid = str(spk["ref_spkid"]),
                is_good   = bool(spk["is_good"]),
                is_bad    = bool(spk["is_bad"])
            ))
        
        return result, spks

    async def get_speakers(self)-> List[str]:
        """Получает спиок спикеров пользователя"""
        if self._client == None: return []

        url: str = "/get-speakers"

        response: httpx.Response = await self._client.get(url)
        response.raise_for_status()

        speakers: List[str] = response.json()
        return speakers
    
    async def set_speaker_title(self, spkid: str, title: str)-> None:
        """Передаёт на сервер присвоеных спикеров"""
        if self._client == None: return
        if not self.current_sid: return

        url: str = "/set-speakers"
        params: Dict[str, str] = {"sid": self.current_sid}
        spks_dict = [{"spkid": spkid, "title": title}]

        response: httpx.Response = await self._client.post(url, params=params, json=spks_dict)
        response.raise_for_status()

    async def _start_get_results(self):
        """Запуск воркера получения результатов"""
        if hasattr(self, '_result_receiver') and self._result_receiver is not None: return
        if self.current_sid is None: return

        from src.net.ws_client import ResultReceiver
        self._result_receiver = ResultReceiver(self.base_url, self.current_sid, self)
        await self._result_receiver.start()

    async def _stop_get_results(self) -> None:        
        """Остановка воркера получения результата"""
        if self._result_receiver is None: return

        await self._result_receiver.stop()    
        self._result_receiver = None

    def add_result(self, data: Dict[str, Any]) ->None:
        cid = int(data["cid"])
        result_data: Dict[str, Any] = data["data"]

        segments, spks = self._prepare_data(result_data, cid)

        size_ms = self.cids.get(cid)
        if size_ms:
            self.result_count += 1
            self.result_ms += size_ms
            self.cids.pop(cid)

        self.on_results(segments, spks)   

        if not self.sid_is_open and self.sended_count == self.result_count:
            asyncio.create_task(self._stop_get_results())

    async def load_results(self) -> None:
        assert self.current_sid is not None
        stts_results, stts_spks = await self.get_session_data(self.current_sid)

        # добавляем только результаты с cid котоые есть в self.cids
        addseg: list[ResultSegment] = []
        cids: list[int] = []

        for result in stts_results:
            if result.cid in self.cids:
                addseg.append(result)
                if not result.cid in cids:
                    cids.append(result.cid)

        for cid in cids:
            size_ms = self.cids.get(cid)
            if size_ms:
                self.result_count += 1
                self.result_ms += size_ms
                self.cids.pop(cid)            

        self.on_results(addseg, stts_spks)  

    async def clean_up(self) -> None:
        """Освобождает ресурсы"""
        await self._stop_get_results()
        if self._client != None: await self._client.aclose()

    async def get_user_roles(self) -> list[str]:
        """Получает список ролей пользователя"""
        if self._client == None: return []

        response: httpx.Response = await self._client.get("/get-user-roles")
        response.raise_for_status()
        
        result: list[str] = response.json()

        return result

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
                connection.api_key = keyring.get_password(f"{SERVICE_NAME}/llmcon/{title}", self.username) or ""
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
            if not self.is_login_ok: return

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
            keyring.set_password(f"{SERVICE_NAME}/llmcon/{title}", self.username, api_key)
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
