from src.config import *
from src.audio_sender import *
from src.file_processor import *
from src.file_scaner import *
from src.result_waiter import *

async def main() -> None:
    config = Config()
    scan_dir_list = get_scan_dir_list(config)

    net_client = NetClient()
    net_client.check_token(config.main_url, "file-server", config.token)

    sender = AudioSender(net_client)

    work_dir = Path(config.work_dir)

    temp_dir = work_dir / "temp"
    temp_dir.mkdir(exist_ok=True)

    wait_result_file = work_dir / "wait_result.txt"
    proced_file = work_dir / "proced_file.txt"

    shared_lock = asyncio.Lock()

    file_processor = FileProcessor(temp_dir, wait_result_file, sender, shared_lock)

    async def on_proc_file(file_path: Path) -> None:
        await file_processor.proc_file(file_path, True, True)

    interval = int(config.scan_interval * 60)
    
    scanner = FileScanner(scan_dir_list, proced_file, interval, on_proc_file)

    result_waiter = ResultWaiter(wait_result_file, net_client, interval, shared_lock)

    await asyncio.gather(
        scanner.start(),
        result_waiter.start(),
    )    

if __name__ == "__main__":
    asyncio.run(main())
