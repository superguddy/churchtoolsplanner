@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "TOOL_DIR=C:\ChurchToolsPlanner\Tool"
set "VENV_DIR=%TOOL_DIR%\.venv"

if not exist "%TOOL_DIR%" mkdir "%TOOL_DIR%"

where py >nul 2>nul
if errorlevel 1 (
    echo Python Launcher 'py' wurde nicht gefunden.
    echo Bitte Python x64 installieren.
    if /I not "%1"=="/nopause" pause
    exit /b 1
)

set "PYLAUNCH="
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
    if not defined PYLAUNCH (
        py -%%V -c "import sys; assert sys.maxsize > 2**32; print(sys.version)" >nul 2>nul
        if not errorlevel 1 set "PYLAUNCH=py -%%V"
    )
)

if not defined PYLAUNCH (
    echo Keine kompatible 64-bit Python-Version gefunden.
    echo Installierte Python-Versionen laut Launcher:
    py -0p
    if /I not "%1"=="/nopause" pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    if exist "%VENV_DIR%" (
        echo Entferne beschaedigte zentrale Umgebung "%VENV_DIR%" ...
        rmdir /s /q "%VENV_DIR%"
    )
    echo Erstelle zentrale Python-Umgebung "%VENV_DIR%" mit %PYLAUNCH% ...
    %PYLAUNCH% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo Konnte zentrale Python-Umgebung nicht erstellen.
        if /I not "%1"=="/nopause" pause
        exit /b 1
    )
) else (
    echo Zentrale Python-Umgebung ist bereits vorhanden: "%VENV_DIR%"
)

set "PY=%VENV_DIR%\Scripts\python.exe"

echo.
echo Aktualisiere pip ...
"%PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo pip-Update fehlgeschlagen.
    if /I not "%1"=="/nopause" pause
    exit /b 1
)

echo.
echo Installiere/aktualisiere Pakete aus requirements.txt ...
"%PY%" -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo Paketinstallation fehlgeschlagen.
    if /I not "%1"=="/nopause" pause
    exit /b 1
)

"%PY%" -c "import fastapi, uvicorn, httpx, tzdata; print('Basispakete OK')"
if errorlevel 1 (
    echo Basispaket-Test fehlgeschlagen.
    if /I not "%1"=="/nopause" pause
    exit /b 1
)

echo.
echo Installation fertig.
echo Zentrale Umgebung: "%VENV_DIR%"
echo Config-Ordner:     "%TOOL_DIR%"
if /I not "%1"=="/nopause" pause
