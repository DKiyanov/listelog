# Listelog

**Speech-to-text with speaker diarization — client/server system for real-time audio transcription.**

Listelog converts speech into text while identifying individual speakers.  
Audio can be captured from microphones, system speakers, or loaded from media files.

The system is designed as a distributed client/server application. Audio processing is performed by specialized workers, while the main server manages sessions, users, processing tasks and speaker profiles.

----------

## Features

-   🎙️ **Microphone capture**
    
-   🔊 **System audio / speaker capture**
    
-   📁 **Processing of audio from media files**
    
-   📝 **Speech-to-text transcription**
    
-   👥 **Speaker diarization**
    
-   🕐 **Speaker segment timestamps**
    
-   👤 **Speaker identification using voice embeddings**
    
-   🔄 **Real-time processing and result delivery**
    
-   💬 **LLM-based processing of the resulting transcript**
    
-   🔐 **LDAP user authentication**
    
-   💾 **Session and audio data storage**
    
-   🎤 **Persistent speaker profiles with voice samples**
    
-   🖥️ **Windows and Linux client**
    
-   🐳 **Docker-based server deployment**
    

The typical end-to-end latency is approximately **25 seconds**.

----------

# Architecture

The system consists of a client application, a main server and several specialized workers.

```text
                         ┌─────────────────────┐
                         │      Client         │
                         │                     │
 Microphone ────────────►│                     │
 System audio ──────────►│      Python/Flet    │
 Media file ────────────►│                     │
                         └──────────┬──────────┘
                                    │
                             HTTP / WebSocket
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │       MAIN          │
                         │                     │
                         │      FastAPI        │
                         │                     │
                         │ • Authentication    │
                         │ • Sessions          │
                         │ • Task management   │
                         │ • Worker management │
                         │ • Storage           │
                         │ • Speaker profiles  │
                         └──────────┬──────────┘
                                    │
                              WebSocket
                                    │
                  ┌─────────────────┼─────────────────┐
                  │                 │                 │
                  ▼                 ▼                 ▼
          ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
          │     STT      │ │     STTD     │ │    SPKEMB    │
          │              │ │              │ │              │
          │ faster-      │ │ MOSS         │ │ WeSpeaker    │
          │ whisper      │ │ Transcribe   │ │ ReDimNet     │
          │              │ │ + diarization│ │              │
          └──────────────┘ └──────────────┘ └──────────────┘
                  │                 │                 │
                  └─────────────────┼─────────────────┘
                                    │
                             Shared filesystem

```

### Components

## Client

**Python + Flet**

The client application provides:

-   microphone audio capture;
    
-   system speaker audio capture;
    
-   loading audio from media files;
    
-   connection to the server;
    
-   online reception of transcription results;
    
-   display of speaker-separated transcripts;
    
-   LLM-based processing of the resulting text.
    

Communication with the server uses:

-   **HTTP** on port `8732`;
    
-   **WebSocket** for receiving processing results.
    

The client can be packaged for both **Windows and Linux**.

For Windows, the distribution is built using:

-   PyInstaller
    
-   Inno Setup
    

----------

## MAIN server

**Python + FastAPI**

The main server is responsible for coordinating the entire processing pipeline.

It provides:

-   client connections;
    
-   user authentication through **LDAP**;
    
-   creation and management of processing sessions;
    
-   receiving audio data from clients;
    
-   task scheduling;
    
-   communication with workers;
    
-   returning processing results to clients;
    
-   storage of recorded sessions;
    
-   storage of speaker profiles;
    
-   management of saved voice embeddings.
    

The server does not perform the computationally expensive AI processing itself. Instead, it distributes tasks among specialized workers.

----------

# Processing workers

## STT — speech-to-text

**Python + faster-whisper**

This worker performs conventional speech recognition without speaker diarization.

It is used when only transcription is required or when speaker identification can be performed separately.

----------

## STTD — speech-to-text with diarization

**C++ + MOSS-Transcribe**

Based on:

-   `mudler/moss-transcribe.cpp-gguf`
    
-   MOSS-Transcribe-Diarize
    

This worker performs both:

1.  speech recognition;
    
2.  segmentation of the recognized speech by speakers.
    

The result contains text segments together with:

-   speaker information;
    
-   segment start time;
    
-   segment end time.
    

MOSS is considerably slower than `faster-whisper`, but it provides speaker diarization as part of the recognition process.

The original model can require substantial GPU memory when processing long audio files. In this project audio is processed in short chunks of **up to 20 seconds**, which significantly reduces the required GPU memory.

----------

## SPKEMB — speaker embeddings

**Python + PyTorch + WeSpeaker**

The worker uses:

`wespeaker-voxceleb-redimnet2-B6-LM`

to extract speaker embeddings from audio segments.

Embeddings are used for:

-   identifying a speakers;
    
-   grouping speaker segments;
    
-   merging speakers detected in different audio chunks;
    
-   matching speakers against previously saved voice profiles.
    

This allows the system to maintain speaker identity across independently processed audio fragments.

For example, if the diarization worker identifies:

```text
segment 1 → speaker A
segment 2 → speaker B
segment 3 → speaker A

```

the embedding worker can determine that the two `speaker A` segments belong to the same person and associate them with a previously known speaker profile when available.

----------

# Worker communication

Communication between the MAIN server and workers is divided into two channels.

### Commands

Commands are exchanged using **WebSocket** connections.

The MAIN server can submit processing tasks and receive their status and results from workers.

### Data

Large audio and processing data are exchanged through a **shared filesystem**.

This avoids transferring large audio files through WebSocket messages and keeps the communication layer lightweight.

```text
MAIN
 │
 ├── WebSocket ───────► Worker
 │        commands
 │
 └── Shared filesystem
          │
          └──────────── audio / results

```

----------

# Processing pipeline

Depending on the selected mode, the processing pipeline can use different workers.

### Simple transcription

```text
Audio
  │
  ▼
STT
  │
  ▼
Text
  │
  ▼
Client

```

### Simple transcription - speaker identification 

At the start of a session, voice embeddings are compared with stored speaker profiles.

```text
Audio
  │
  ▼
SPKEMB
  │
  ▼
Speaker embedding
  │
  ▼
Saved speaker profiles
  │
  ▼
Known speaker / unknown speaker

```

### Transcription with speaker diarization

```text
Audio
  │
  ▼
STTD
  │
  ├── text segments
  ├── timestamps
  └── speaker segments
          │
          ▼
       SPKEMB
          │
          ▼
   Unified speaker list
          │
          ▼
        Client

```

----------

# Quick Start

The easiest way to start the server is to use the prepared Quick Start package.

### 1. Download

Download and unpack:

```text
listelog_QuickStart.zip

```

### 2. Prepare the server

Run:

```bash
./prepare.sh

```

This downloads the required AI models.

### 3. Start the server

Run:

```bash
./start_server.sh

```

The first startup can take some time because the required Docker images have to be downloaded.

### 4. Open the web interface

Open:

```text
http://localhost:8732/

```

The web interface provides access to the client distribution.

Download and install the client application.

----------

# Requirements

The server uses Docker containers and requires a compatible NVIDIA GPU for AI processing.

A typical deployment consists of:

```text
MAIN
STT
STTD
SPKEMB
```

Each component runs in its own container.

> GPU requirements depend on the selected models and worker configuration.

The architecture allows workers to be deployed independently, making it possible to distribute processing across different machines.

----------

# Project structure

A simplified project structure:

```text
listelog/
│
├── server/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── ...
│
├── workers/
│   ├── stt/
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   └── ...
│   │
│   ├── sttd-moss/
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   └── ...
│   │
│   └── spkemb/
│       ├── Dockerfile
│       ├── docker-compose.yml
│       └── ...
│
├── client/
│   └── ...
│
└── ...

```

----------

# Technology
Python
Flet
FastAPI
faster-whisper
C++
MOSS-Transcribe
Client ↔ Server
HTTP + WebSocket
Server ↔ Workers
LDAP
Docker
PyInstaller + Inno Setup

----------

# AI models

The project currently uses the following models:

### Voice Activity Detection
Silero-VAD

### Speech recognition / diarization
`mudler/moss-transcribe.cpp-gguf`

### Speaker embeddings
`wespeaker/wespeaker-voxceleb-redimnet2-B6-LM`

The models are downloaded automatically by `prepare.sh`.

----------

# LLM processing


The recognized transcript can be passed to an LLM for further processing.

The client application provides a dedicated interface for working with the resulting text.

Users can independently configure LLM prompts and connections.

The transcription and LLM processing stages are intentionally separated: the speech processing server generates the initial transcript, while subsequent text processing can be performed independently.

----------

# Speaker profiles

Listelog can maintain a collection of known speakers.

A speaker profile contains:

-   a speaker name;  
-   extracted voice embeddings.
    

During processing, newly extracted embeddings can be compared with saved profiles.

This makes it possible to replace anonymous speaker labels with known names when a sufficiently close voice match is found.

----------

# Real-time processing

Audio is processed incrementally rather than waiting for the entire recording to finish.

The client sends audio to the server, the server distributes processing tasks to workers, and the resulting transcript is returned to the client through WebSocket.

Typical response latency is approximately:

**~25 seconds**

The exact latency depends on:

-   GPU performance;
    
-   number of simultaneously active workers;
    
-   audio characteristics;
    
-   selected processing mode;
    
-   server load.
    
----------

# Credits

This project uses and builds upon several open-source projects:

-   Faster-whisper
-   Silero-VAD
-   MOSS-Transcribe-Diarize
-   Mudler/moss-transcribe.cpp-gguf
-   WeSpeaker
-   PalabraAI/redimnet2
-   Flet

Many thanks to the authors and maintainers of these projects.

----------

# Status

The project is currently under active development.

The architecture, processing pipeline and model selection may change as performance and recognition quality are further optimized.

Feedback, bug reports and suggestions are welcome.