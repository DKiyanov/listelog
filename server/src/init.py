from src.config import Config
from src.user_manager import *
from src.stt_session_reader import *

from src.dispatcher import *
from src.client_manager import *
from src.workers_manager import *
from src.lost_task_controller import *

config = Config()

user_manager = UserManager(config)
stt_session_reader = SttSessionReader(config)

dispatcher = TaskDispatcher(config)
client_manager = ClientManager(config)
workers_manager = WorkersManager(dispatcher, client_manager)
lost_task_controller = LostTasksController(config.audio_data_dir, workers_manager, dispatcher)

def _init_directories(config: Config) -> None:
    for directory in [config.audio_data_dir, config.sessions_dir, config.users_dir]:
        Path(directory).mkdir(parents=True, exist_ok=True)

    audio_data_dir = Path(config.audio_data_dir)
    Path.mkdir(audio_data_dir / "stt", exist_ok=True)
    Path.mkdir(audio_data_dir / "sttd", exist_ok=True)

_init_directories(config)
