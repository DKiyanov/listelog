#!/bin/sh

set -e

echo "=== Запуск скрипта скачивания моделей ==="

python3 download_models.py 

echo "=== Запуск скрипта скачивания дистрибутивов клиентов ==="

python3 download_clients.py

echo "=== Процесс завершен! ==="