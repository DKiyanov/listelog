# syntax=docker/dockerfile:1

FROM nvidia/cuda:12.8.1-devel-ubuntu24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    cmake \
    build-essential \
    pkg-config \
    libopus-dev \
    libopusfile-dev \	
	libboost-all-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth 1 --recurse-submodules \
    https://github.com/mudler/moss-transcribe.cpp.git .
	
COPY moss-batch.cpp /src/moss-batch.cpp
COPY moss-worker.cpp /src/moss-worker.cpp

RUN cat << 'EOF' >> CMakeLists.txt

find_package(PkgConfig REQUIRED)
pkg_check_modules(OPUSFILE REQUIRED opusfile)

add_executable(moss-batch moss-batch.cpp)
target_include_directories(moss-batch PRIVATE include src third_party ${OPUSFILE_INCLUDE_DIRS})
target_link_libraries(moss-batch PRIVATE moss-transcribe ${OPUSFILE_LIBRARIES})

add_executable(moss-worker moss-worker.cpp)
target_include_directories(moss-worker PRIVATE include src third_party ${OPUSFILE_INCLUDE_DIRS})
target_link_libraries(moss-worker PRIVATE moss-transcribe ${OPUSFILE_LIBRARIES})
EOF
	
RUN cmake -B build \
    -DMT_GGML_CUDA=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_EXE_LINKER_FLAGS="-L/usr/local/cuda/lib64/stubs -lcuda"

RUN cmake --build build --config Release -j$(nproc)


# =========================================================
# Runtime
# =========================================================

FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    ffmpeg \
    ca-certificates \
	libopusfile0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# MOSS executable
COPY --from=builder \
    /src/build/moss-transcribe \
    /app/moss-transcribe

COPY --from=builder \
    /src/build/moss-batch \
    /app/moss-batch

COPY --from=builder \
    /src/build/moss-worker \
    /app/moss-worker

# ggml core libraries
COPY --from=builder \
    /src/build/third_party/ggml/src/libggml.so.0.15.3 \
    /app/lib/libggml.so.0

COPY --from=builder \
    /src/build/third_party/ggml/src/libggml-cpu.so.0.15.3 \
    /app/lib/libggml-cpu.so.0

COPY --from=builder \
    /src/build/third_party/ggml/src/libggml-base.so.0.15.3 \
    /app/lib/libggml-base.so.0

# ggml CUDA backend
COPY --from=builder \
    /src/build/third_party/ggml/src/ggml-cuda/libggml-cuda.so.0.15.3 \
    /app/lib/libggml-cuda.so.0

ENV LD_LIBRARY_PATH="/app/lib:${LD_LIBRARY_PATH}"

ENTRYPOINT ["/app/moss-transcribe"]