from .file_processor import *
import platform

@dataclass(frozen=True)
class WorkerConig(AbstractConfig):
    mode: str # ws - работа с MAIN через web-socket; fs - сканирование каталога
    
    main_url: str
    wait_time: int # Время ожидания основного сервера в минутах

    audio_data_dir: str
    sessions_dir: str
    users_dir: str

    next_work_type: str  # следущий тип-работы/подкаталог череди audio_data_dir
    verbose: bool

    def __init__(self, config_path: str) -> None:
        super().__init__(config_path)

def get_worker_config_path() -> str:
    if len(sys.argv) > 1:
        config_path = sys.argv[1] 
        if config_path:
            return config_path

    env_value = os.getenv("AUDIO_DATA_DIR")
    if env_value is None:
        env_value = os.getenv("audio_data_dir")

    if env_value is not None:
        return "env"
    
    config_path: str = f"./config_.json"
    if os.path.exists(config_path):
        return config_path

    os_type = platform.system()

    if os_type == "Windows":
        config_path: str = f"./config_windows.json"
        if os.path.exists(config_path):
            return config_path
        
    if os_type == "Linux":
        config_path: str = f"./config_linux.json"
        if os.path.exists(config_path):
            return config_path 

    return ""