@echo off
setlocal
cd /d "%~dp0"

set "TOOL_DIR=C:\ChurchToolsPlanner\Tool"
set "VENV_DIR=%TOOL_DIR%\.venv"
set "CT_PLANNER_TOOL_DIR=%TOOL_DIR%"

if not exist "%TOOL_DIR%" mkdir "%TOOL_DIR%"

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Zentrale Python-Umgebung fehlt. Installation wird jetzt automatisch ausgefuehrt...
    echo.
    call "%~dp0installieren.bat" /nopause
    if errorlevel 1 (
        echo.
        echo Installation fehlgeschlagen.
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"

rem Bestehende .venv kann aus einem aelteren Build stammen: Pflichtmodule pruefen
rem und nur bei Bedarf requirements nachziehen.
python -c "import fastapi, uvicorn, httpx, tzdata" >nul 2>&1
if errorlevel 1 (
    echo Aktualisiere Python-Abhaengigkeiten fuer diesen Build...
    python -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo Abhaengigkeiten konnten nicht aktualisiert werden.
        pause
        exit /b 1
    )
)

python "%~dp0app.py"
pause
