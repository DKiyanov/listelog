# запуск Ubuntu в wsl
wsl -d Ubuntu-24.04 

# Переход в каталог проекта
cd /mnt/c/python_projects/listelog/client

# Активация виртуального оружения
source .linux_venv/bin/activate

# Запуск скрипта (перед запуском ативация окружения не обязательна - он сам активирует)
./build_for_linux.sh


