from src.init import config
from src.user_manager import UserManager
import shutil
import json
import random
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Union
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPBasic, HTTPBearer, HTTPBasicCredentials, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, FileResponse
from contextlib import asynccontextmanager
from pydantic import BaseModel, Field
import uvicorn

from src.init import *
from src.ws_routes import router as ws_router

from src.user_authentication import user_authentication

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Код, выполняемый при старте
    await dispatcher.start()
    await lost_task_controller.start()
    print("lifespa - сервисы запущены")
    
    yield  # Здесь приложение работает
    
    # Код, выполняемый при остановке (shutdown)


_app = FastAPI(title="Audio Processing API", version="1.0.0", lifespan=lifespan)
_basic_security = HTTPBasic()
_security = HTTPBearer()

# --- конфигурация путей ---
_AUDIO_DATA_DIR = Path(config.audio_data_dir).resolve()
_SESSIONS_DIR = Path(config.sessions_dir).resolve()
_USERS_DIR= Path(config.users_dir).resolve()
_SITE_DIR= Path(config.site_dir).resolve()

_app.include_router(ws_router)


def start_server():
    # Запуск web сервера
    uvicorn.run(_app, host="0.0.0.0", port=config.port)

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(_security)) -> str:
    """Зависимость для проверки авторизации в эндпоинтах."""
    token: str = credentials.credentials
    user: Optional[str] = user_manager.get_user(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# --- Схемы Pydantic (Валидация данных) ---
class OpenSessionRequest(BaseModel):
    title: str = Field(..., description="Наименование/заголовок беседы")

class OpenSessionResponse(BaseModel):
    sid: int = Field(..., description="Уникальный идентификатор сессии")

class SegmentResult(BaseModel):
    tss: int
    tse: int
    spkid: str
    text: str

class SessionResultItem(BaseModel):
    cid: int
    segments: List[SegmentResult]

class SpeakerItem(BaseModel):
    spkid: str
    title: str

class GetResultsResponse(BaseModel):
    wait_count: int
    spks: List[SpeakerItem]
    results: List[SessionResultItem]

class SttSessionData(BaseModel):
    results: List[SttSegment]
    spks: List[SttSpeaker]

# --- Вспомогательные функции бизнес-логики ---
def generate_unique_sid() -> int:
    """Генерирует уникальный случайный int (до 6 символов), которого нет в sessions."""
    for _ in range(1000):
        candidate: int = random.randint(100000, 999999)
        session_path: Path = _SESSIONS_DIR / str(candidate)
        if not session_path.exists():
            return candidate
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to generate unique session ID",
    )

def find_session_owner(sid: int) -> Optional[str]:
    """Ищет владельца сессии в активных сессиях или архиве."""
    active_head: Path = _SESSIONS_DIR / str(sid) / "session_head.json"
    if active_head.exists():
        try:
            with open(active_head, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
                return data.get("user")
        except (json.JSONDecodeError, IOError):
            return None

def login_ex(username: str, password: str)->bool:
    """Проверяет что пользователь существует"""

    is_authenticated = user_authentication(config, username, password)
    return is_authenticated
    
def _check_sid_user(sid: int, user: str) -> None:
    session_path: Path = _SESSIONS_DIR / str(sid)
    if not session_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    owner: Optional[str] = find_session_owner(sid)
    if owner and owner != user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") 
    
# --- API Эндпоинты ---
@_app.post("/get-token", summary="Аутентификация и получение токена")
def get_token(
    credentials: HTTPBasicCredentials = Depends(_basic_security)
) -> Dict[str, str]:
    """Принимает Basic Auth, проверяет в LDAP, возвращает новый Bearer токен."""
    username: str = credentials.username
    password: str = credentials.password

    if not login_ex(username, password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Basic"},
        )

    token: str = user_manager.generate_user_token(login=username)

    print(f"get_token {token}")
    return {"access_token": token, "token_type": "bearer"}

@_app.post("/check-token", status_code=status.HTTP_200_OK)
async def check_token(
    user: str = Depends(verify_token)
) -> Dict[str, str]:
    """Проверяет переданный в заголовке token."""
    return {"status": "authenticated", "user": user}

@_app.post("/open-session", response_model=OpenSessionResponse, status_code=status.HTTP_200_OK)
async def open_session(
    payload: OpenSessionRequest, 
    user: str = Depends(verify_token)
) -> OpenSessionResponse:
    """Создает новую сессию, инициализирует структуру папок и метаданные."""
    sid: int = generate_unique_sid()

    # Создает каталог сессии и файл session_head.json 
    session_path: Path = _SESSIONS_DIR / str(sid)
    session_path.mkdir(parents=True, exist_ok=True)

    session_info_path: Path = session_path / "info"
    session_info_path.mkdir(parents=True, exist_ok=True)

    session_proc_path: Path = session_path / "proc"
    session_proc_path.mkdir(parents=True, exist_ok=True)

    head_data: Dict[str, str] = {
        "title": payload.title,
        "user": user,
        "date_time": datetime.now(timezone.utc).isoformat()  
    }
    
    with open(session_path / "session_head.json", "w", encoding="utf-8") as f:
        json.dump(head_data, f, ensure_ascii=False, indent=4)

    user_manager.user_log_add(user, "OPEN", str(sid), payload.title)

    return OpenSessionResponse(sid=sid)

@_app.post("/upload-chunk", status_code=status.HTTP_202_ACCEPTED)
async def upload_chunk(
    request: Request,
    sid: int = Query(...),
    cid: int = Query(...),
    src: int = Query(...),
    tss: int = Query(...),
    tse: int = Query(...),
    diarize: bool = Query(...),
    user: str = Depends(verify_token)
) -> Dict[str, str]:
    """Принимает бинарный аудио-поток и сохраняет метаданные блока."""
    _check_sid_user(sid, user)

    session_path: Path = _SESSIONS_DIR / str(sid)

    # Чтение бинарного тела запроса
    body_bytes: bytes = await request.body()

    file_name: str = f"{sid}_{cid}.ogg"

    work_type: str = "sttd" if diarize else "stt"
    
    # Сохранение сырых аудио-данных
    audio_file_path: Path = _AUDIO_DATA_DIR / work_type / file_name

    with open(audio_file_path, "wb") as f:
        f.write(body_bytes)

    # Сохранение метаданных блока
    chunk_info: Dict[str, Union[int, bool]] = {
        "src": src,
        "tss": tss,
        "tse": tse,
        "diarize": diarize
    }

    chunk_info_path: Path = session_path / "info" / f"{cid}_info.json"

    with open(chunk_info_path, "w", encoding="utf-8") as f:
        json.dump(chunk_info, f, ensure_ascii=False, indent=4)

    await dispatcher.add_task(Task(work_type, str(sid), str(cid), file_name, ""))

    return {"status": "accepted"}

@_app.post("/close-session", status_code=status.HTTP_200_OK)
async def close_session(
    sid: int = Query(...), 
    user: str = Depends(verify_token)
) -> Dict[str, str]:
    """Закрывает сессию"""
    _check_sid_user(sid, user)
    user_manager.user_log_add(user, "CLOSE", str(sid))
    return {"status": f"session closed"}

        
@_app.post("/delete-session", status_code=status.HTTP_200_OK)
async def delete_session(
    sid: int = Query(...), 
    user: str = Depends(verify_token)
) -> Dict[str, str]:
    """Полностью удаляет все данные сессии как из рабочего пространства, так и из архива."""
    _check_sid_user(sid, user)

    # Переменная-флаг для отслеживания того, нашли ли мы вообще что-то для удаления
    was_found: bool = False

    # 2. Удаление из рабочих сессий
    active_session_path: Path = _SESSIONS_DIR / str(sid)
    if active_session_path.exists():
        shutil.rmtree(active_session_path)
        was_found = True

    # 3. Удаление связанных аудио файлов
    if _AUDIO_DATA_DIR.exists():
        for file in _AUDIO_DATA_DIR.glob(f"{sid}_*.ogg"):
            file.unlink()
            was_found = True  # Если нашли сиротские аудиофайлы, считаем сессию частично найденной

    # 4. Если ни в одном месте файлы сессии не были обнаружены — отдаем 404
    if not was_found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found anywhere")

    user_manager.user_log_add(user, "DEL", str(sid))

    # Теперь у линтера нет сомнений: либо сработает raise выше, либо этот return
    return {"status": "session totally deleted"}

@_app.get("/get-proced-audio", status_code=status.HTTP_200_OK)
async def get_proced_audio(
    sid: int = Query(...), 
    cid: int = Query(...), 
    user: str = Depends(verify_token)
) -> FileResponse:   
    """Возвращает данне ранее обработанного аудио-файла"""
    _check_sid_user(sid, user)

    file_path: Path = _SESSIONS_DIR / str(sid) / "proc" / f"{sid}_{cid}.ogg"

    # Проверяем, существует ли файл на диске
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
        
    # Возвращаем файл клиенту
    return FileResponse(
        path=file_path, 
        filename=f"{sid}_{cid}.ogg",  # Имя, с которым файл сохранится у пользователя
        media_type="audio/ogg"
    )
        
@_app.post("/reset-audio", status_code=status.HTTP_200_OK)
async def reset_audio(
    sid: int = Query(...), 
    cid: int = Query(...), 
    user: str = Depends(verify_token)
) -> None:   
    """Устанавливает файл на повторную обработку"""
    _check_sid_user(sid, user)

    session_path: Path = _SESSIONS_DIR / str(sid)
    file_name = f"{sid}_{cid}.ogg"
    
    src_pfile: Path = session_path / "proc" / file_name

    # Проверяем, существует ли файл на диске
    if not src_pfile.exists():
        raise HTTPException(status_code=404, detail="File not found") 

    chunk_info_file: Path = session_path / "info" / f"{cid}_info.json"

    with open(chunk_info_file, "r", encoding="utf-8") as f:
        chunk_data: dict[str, Any] = json.load(f)

    diarize: bool = chunk_data.get("diarize", False)
    
    work_type: str = "sttd" if diarize else "stt"
    
    dest_pfile: Path = _AUDIO_DATA_DIR / work_type / file_name

    shutil.copy(src_pfile, dest_pfile) # Перезпишет

@_app.get("/get-stt-sessions", response_model=List[SttSession], status_code=status.HTTP_200_OK)
async def get_stt_sessions(
    user: str = Depends(verify_token)
) -> List[SttSession]:
    """Возвращает список STT сессий пользователя"""
    return stt_session_reader.get_user_sessions(user) 

@_app.get("/get-stt-ses-data", response_model=SttSessionData, status_code=status.HTTP_200_OK)
async def get_stt_ses_data(
    sid: int = Query(...), 
    user: str = Depends(verify_token)
) -> SttSessionData:
    """Возвращает данные сессии"""
    _check_sid_user(sid, user)

    results, spks = stt_session_reader.get_session_data(str(sid))
    return SttSessionData(results=results, spks=spks)

@_app.get("/get-speakers", response_model=List[str], status_code=status.HTTP_200_OK)
async def get_speakers(
    user: str = Depends(verify_token)
) -> List[str]:
    """Получает список спикеров пользователя."""
    
    speakers_file = _USERS_DIR / user / "speakers.json"
    if not speakers_file.exists():
        return []

    with open(speakers_file, "r", encoding="utf-8") as f:
        speakers_data: list[dict[str, Any]] = json.load(f)

    titles: List[str] = sorted(set(s["title"] for s in speakers_data), key=str.lower)        

    return titles

@_app.post("/set-speakers", status_code=status.HTTP_200_OK)
async def set_speakers(
    payload: List[SpeakerItem], 
    sid: int = Query(...), 
    user: str = Depends(verify_token)
) -> Dict[str, Any]: 
    """Обновляет список спикеров пользователя."""
    _check_sid_user(sid, user)

    condidats: Dict[str, List[Dict[str, Any]]] = {}
    speakers_file = _USERS_DIR / user / "speakers.json"

    update_need: bool = False

    if speakers_file.exists():
        with open(speakers_file, "r", encoding="utf-8") as f:
            speakers_data: list[dict[str, Any]] = json.load(f)
    else:
        speakers_data: list[dict[str, Any]] = []

    for new_speaker in payload:
        spkid_parts = new_speaker.spkid.split("/", 1)
        src = spkid_parts[0]
        spknum = int(spkid_parts[1])

        if not src in condidats:
            condidats_file = _SESSIONS_DIR / str(sid) / f"condidats_{src}.json"
            with open(condidats_file, "r", encoding="utf-8") as f:
                condidats_data: list[dict[str, Any]] = json.load(f)  
            condidats[src] = condidats_data         

        condidats_data = condidats[src]
        condidat = next((condidat for condidat in condidats_data if condidat["spknum"] == spknum), None)
        if condidat is None: continue
        
        is_good: bool = condidat["is_good"]
        if not is_good: continue

        emb = condidat["emb"]

        speaker = next((speaker for speaker in speakers_data if speaker["emb"] == emb), None)
        if speaker is not None:
            if speaker["title"] != new_speaker.title:
                speaker["title"] = new_speaker.title
                update_need = True
        else:
            speakers_data.append({"title": new_speaker.title, "emb": emb})
            update_need = True
    
    if update_need:
        with open(speakers_file, "w", encoding="utf-8") as f:
            json.dump(speakers_data, f, ensure_ascii=False, indent=4)    

    return {"status": "success"}      

# Эндпоинт для отображения главной страницы
@_app.get("/", response_class=HTMLResponse)
async def read_root():
    with open(f"{_SITE_DIR}/index.html", "r", encoding="utf-8") as f:
        return f.read()   
          
# Эндпоинт для скачивания файлов: клиент для Windows/Linux
@_app.get("/assets/{file_path:path}")
async def download_file(file_path: str):
    # Safe path resolution: объединяем пути и переводим в абсолютный вид
    assets_dir = _SITE_DIR / "assets"

    safe_path = (assets_dir / file_path).resolve()

    # Защита от Path Traversal: проверяем, что итоговый путь начинается с ASSETS_DIR
    if not safe_path.is_relative_to(assets_dir):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Доступ запрещен"
        )

    # Проверяем, существует ли файл и не является ли он директорией
    if not safe_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Файл не найден"
        )

    # Возвращаем файл. Параметр filename заставит браузер именно скачивать файл
    return FileResponse(
        path=safe_path, 
        filename=safe_path.name, 
        media_type="application/octet-stream"
    )      

if __name__ == "__main__":
    start_server()
