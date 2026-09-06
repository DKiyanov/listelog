# ==============================================================================
# ЭТАП 1: БАЗОВЫЙ ОБРАЗ (Тяжелые ML зависимости. Собирается один раз)
# ==============================================================================
FROM pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime AS base-stage

# Глобально отключаем блокировку внешней среды для pip (PEP 668)
ENV PIP_BREAK_SYSTEM_PACKAGES=1

# Подавляем интерактивные запросы при установке пакетов Ubuntu
ENV DEBIAN_FRONTEND=noninteractive

# Установка системных утилит
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Доставляем torchaudio, строго соответствующий CUDA 13.0
RUN pip install --no-cache-dir torchaudio --index-url https://pytorch.org

# Устанавливаем wespeaker
RUN pip install --no-cache-dir git+https://github.com/wenet-e2e/wespeaker.git

# ==============================================================================
# ЭТАП 2: РАБОЧИЙ ОБРАЗ (Код проекта. Пересобирается мгновенно)
# ==============================================================================
FROM base-stage AS worker-stage

WORKDIR /app

# Установка зависимостей вашего проекта
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Устанавливаем listelog_pyworker_lib
RUN pip install --no-cache-dir git+https://github.com/DKiyanov/listelog.git@main#subdirectory=workers/listelog_pyworker_lib

# Копирование исходного кода
COPY . .

CMD ["python", "worker_spkemb_moss.py"]
