from pathlib import Path
import psutil
import os
import sys

def get_scan_folders() -> list[Path]:
    result: list[Path] = []
    scan_file = Path("scaning_filders.txt")
    if scan_file.exists():
        with scan_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    result.append(Path(line))   
        return result
    
    if os.getenv("SCAN_INTERVAL"):
        return _get_volumes()
    
    for arg in sys.argv[1:]:
        xpath = Path(arg)
        if xpath.is_dir():
            result.append(xpath)
    
    return result

def _get_volumes() -> list[Path]:
    # Исключаем виртуальные ФС и внутренности Docker
    ignored_fstypes = {'proc', 'sysfs', 'tmpfs', 'devpts', 'cgroup', 'mqueue', 'overlay'}
    ignored_paths = {'/etc/resolv.conf', '/etc/hostname', '/etc/hosts'}

    volumes: list[Path] = []
    for part in psutil.disk_partitions(all=True): # all=True важно для чтения всех mountpoints
        # Отсекаем системный мусор
        if part.fstype in ignored_fstypes:
            continue
        if part.mountpoint in ignored_paths or part.mountpoint.startswith(('/sys', '/proc', '/dev')):
            continue
            
        volumes.append(Path(part.mountpoint))
    return volumes

