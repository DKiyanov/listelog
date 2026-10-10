# запуск Ubuntu в wsl
wsl -d Ubuntu-24.04 

# Переход в каталог проекта
cd /mnt/c/python_projects/listelog/client

# Активация виртуального оружения
source .linux_venv/bin/activate

# Запуск из исходников
python client.py

# Запуск скрипта сборки дистрибутива (перед запуском ативация окружения не обязательна - он сам активирует)
./build_for_linux.sh

# Место установки
cd /home/<user name>/.local/opt/ListeLog
