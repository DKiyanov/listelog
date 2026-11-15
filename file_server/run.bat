@echo off
cd /d "%~dp0"
chcp 65001 > nul
.venv\Scripts\python.exe file_server.py
pause
