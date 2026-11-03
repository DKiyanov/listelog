import asyncio
from typing import List, Optional, Dict, Any, Tuple
import httpx
import asyncio
import io

from src.models import *

class NetClient:
    """Сетевой клиент для взаимодействия с сервером STT."""

    def __init__(self) -> None:
        self.on_send: SimpleCallBack | None = None
        self.on_results: OnResult | None = None    

        self.is_login_ok = False
        self._client: Optional[httpx.AsyncClient] = None
        self.base_url: str = ""
        self.username: str = ""
        self.token: str = ""        

        self.current_sid: Optional[str] = None
        self.sid_is_open: bool = False

        self.sended_count: int = 0 # Количество отправленых пакетов в рамках сессии
        self.sended_ms: int = 0 # Сколько отправлено на сервер за сессию
        
        self.result_count: int = 0 # Количество полученых результатов за сессию
        self.result_ms: int = 0 # Сколько получено результата 

        self.cids: Dict[int, int] = {} # cid -> длительность звука мс

    def set_callbacks(self, on_send: SimpleCallBack, on_results: OnResult | None) -> None:
        self.on_send = on_send
        self.on_results = on_results            

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

    def get_token(self, base_url: str, username: str, password: str) -> str:
        url = f"{base_url.rstrip('/')}/get-token"
        auth = (username, password)

        # Отправка POST-запроса с Basic Auth
        response = httpx.post(url, auth=auth)
        response.raise_for_status()

        # Извлечение токена из ответа
        data = response.json()
        token = data.get("access_token")
        token_type = data.get("token_type")

        if not token or not token_type or token_type != "bearer":
            raise ValueError("an invalid token was received")

        self._set_login_ok(base_url, username, token)

        return token

    def check_token(self, base_url: str, username: str, token:str):
        url = f"{base_url.rstrip('/')}/check-token"
        headers = {"Authorization": f"Bearer {token}"}

        response = httpx.post(url, headers=headers)
        response.raise_for_status()

        self._set_login_ok(base_url, username, token)
      

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
        assert self.current_sid
        assert self.on_send
        if self._client == None: return

        if not self.current_sid:
            raise RuntimeError("Session is not open")

        url: str = "/upload-chunk"
        params: Dict[str, Any] = {
            "sid": self.current_sid,
            "cid": cid,
            "src": packet.source_id,
            "tss": packet.ts_start,
            "tse": packet.ts_end,
            "bad": packet.is_bad,
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
        assert self.on_results

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
        assert self.on_results
        assert self.current_sid

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

    