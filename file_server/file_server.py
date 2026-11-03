from pathlib import Path

from src.config import *
from src.scan_folders import *
from src.client_main import *
from src.file_scanner import *

# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------
async def main() -> None:
    config = Config()
    folders = get_scan_folders()
    ensure_dirs(config)
    net_client = NetClient()
    net_client.check_token(config.main_url, "file-server", config.token)
    sender = AudioSender(net_client)

    async def send_audio(file_path: Path, title: str) -> str:
        save_audio_path = file_path.parent.parent / SUBDIRS[STATUS_PROCESSING] / f"{file_path.stem}.ogg"
        result = await sender.file_upload(str(file_path), title, str(save_audio_path), True, True)
        return result

    server = FileServer(config, folders, send_audio)

    loop = asyncio.get_running_loop()
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, server.stop)
        except NotImplementedError:
            pass

    log.info("Запуск сервера. data_dir=%s, info_dir=%s, interval=%s мин",
             config.data_dir, config.info_dir, config.scan_interval)
    await server.run()
    log.info("Сервер остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass