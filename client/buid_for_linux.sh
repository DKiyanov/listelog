#!/bin/bash

# Получение абсолютного пути к текущей папке скрипта
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/"

# Путь к вашей папке venv (измените '.venv' на имя вашей папки, если оно другое)
VENV_PATH="${PROJECT_DIR}.venv"

# Проверка наличия venv и его активация
if [ -f "${VENV_PATH}/bin/activate" ]; then
    echo "Activating virtual environment..."
    source "${VENV_PATH}/bin/activate"
else
    echo "Error: Virtual environment not found at ${VENV_PATH}"
    read -p "Press [Enter] key to exit..."
    exit 1
fi

# Устанавливаем переменную окружения для Flet
export FLET_VIEW_PATH="${PROJECT_DIR}flet_client/empty"

# Сборка проекта через PyInstaller
echo "Starting PyInstaller build..."
pyinstaller client_linux.spec

# Деактивация виртуального окружения
deactivate

# Ожидание нажатия клавиши
echo ""
echo "Build complete."
read -p "Press [Enter] key to continue..."

