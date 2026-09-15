from src.config import Config
from src.user_manager import *
from src.stt_session_reader import *

from src.dispatcher import *
from src.client_manager import *
from src.workers_manager import *
from src.lost_task_controller import *

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

config = Config()

user_manager = UserManager(config)
stt_session_reader = SttSessionReader(config)

dispatcher = TaskDispatcher(config)
client_manager = ClientManager(config)
workers_manager = WorkersManager(dispatcher, client_manager)
lost_task_controller = LostTasksController(config.audio_data_dir, workers_manager, dispatcher)

_security = HTTPBearer()

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

def _init_directories(config: Config) -> None:
    for directory in [config.audio_data_dir, config.sessions_dir, config.users_dir]:
        Path(directory).mkdir(parents=True, exist_ok=True)

    audio_data_dir = Path(config.audio_data_dir)
    Path.mkdir(audio_data_dir / "stt", exist_ok=True)
    Path.mkdir(audio_data_dir / "sttd", exist_ok=True)

_init_directories(config)
