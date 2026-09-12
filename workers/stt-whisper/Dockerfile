# Этап 1
FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04 AS base-stage

RUN apt-get update && apt-get install -y \
    python3-pip \
	git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir faster-whisper==1.2.1

RUN pip install --no-cache-dir numpy

# Этап 2
FROM base-stage AS worker-stage

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Устанавливаем listelog_pyworker_lib
RUN pip install --no-cache-dir git+https://github.com/DKiyanov/listelog.git@main#subdirectory=workers/listelog_pyworker_lib

COPY . .

CMD ["python3", "worker_stt_whisper.py"]