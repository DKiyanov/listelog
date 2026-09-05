@echo off
setlocal

:: Сборка для windows
:: сбрку запускаем в VirtualBox
:: из wine сборка получается не корректной

:: Получение абсолютного пути к текущей папке скрипта
set "PROJECT_DIR=%~dp0"

:: Устанавливаем путь к клиенту, чтоб pyinstaller его не скачивал, 
:: папка пустая - это чтобы в дубля не было
:: мы сами flet_client\windows упакуем см. client_windows.spec
set "FLET_VIEW_PATH=%PROJECT_DIR%flet_client\empty"

:: Сборка проекта через PyInstaller
pyinstaller client_windows.spec

:: Ожидание нажатия клавиши перед закрытием окна
echo.
echo Build complit.
pause

endlocal
