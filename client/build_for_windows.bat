@echo off
setlocal enabledelayedexpansion

:: Windows build
:: Run the build in VirtualBox
:: The build produced through Wine is not correct

:: Get the absolute path to the current script directory
set "PROJECT_DIR=%~dp0"

:: Check whether ISCC (Inno Setup) is available in PATH
where ISCC >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Please install "Inno Setup" and add its path to the PATH environment variable.
    pause
    exit /b
)

:: Check whether the virtual environment (.venv) exists
if not exist "%PROJECT_DIR%.venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found in:
    echo %PROJECT_DIR%.venv
    pause
    exit /b
)

:: Activate the virtual environment
echo Activating virtual environment...
call "%PROJECT_DIR%.venv\Scripts\activate.bat"

set WORKPATH=xbuild\windows\build
set DISTPATH=xbuild\windows\dist
set OUTPUT_FILE=xbuild\windows\ListeLog_win.exe

pip install --upgrade pyinstaller

:: Set the client path so that PyInstaller does not download it
:: The folder is empty to avoid duplication
:: We package flet_client\windows ourselves, see client_windows.spec
set "FLET_VIEW_PATH=%PROJECT_DIR%flet_client\empty"

:: Build the project using PyInstaller
python -m PyInstaller --workpath %WORKPATH% --distpath %DISTPATH% --noconfirm client_windows.spec

:: Deactivate the virtual environment
if defined VIRTUAL_ENV call deactivate

:: Delete the old file if it exists
if exist %OUTPUT_FILE% (
del /f /q %OUTPUT_FILE%
)

:: Build the installer
echo Starting Inno Setup build...
ISCC windows_installer_script.iss
if %errorlevel% neq 0 (
    echo [ERROR] An error occurred while compiling with ISCC.
    pause
    exit /b
)

:: Check whether the new file was created
if not exist %OUTPUT_FILE% (
    echo [ERROR] The build completed, but %OUTPUT_FILE% was not found.
    pause
    exit /b
)

:: Copy the file to the website assets directory if it exists
if exist "..\site\assets" (
    echo Copying the file to ..\site\assets...
    xcopy /y %OUTPUT_FILE% "..\site\assets" >nul
)

echo Build completed successfully!

pause

endlocal
