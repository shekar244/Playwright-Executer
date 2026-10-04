@echo off
REM ============================================================
REM  Jira Report Builder - Windows launcher
REM  Run:  run.bat            (set PORT=8600 first to change port)
REM  Uses an existing .venv or venv folder (in that order), or the folder
REM  named by VENV_DIR; otherwise the first run creates .\venv.
REM  Missing requirements are installed automatically.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8501

REM -- Pick the virtual environment --------------------------------------
set "VENV="
if defined VENV_DIR set "VENV=%VENV_DIR%"
if not defined VENV if exist ".venv\Scripts\python.exe" set "VENV=.venv"
if not defined VENV if exist "venv\Scripts\python.exe" set "VENV=venv"
if not defined VENV set "VENV=venv"

if not exist "%VENV%\Scripts\python.exe" (
    set "PYTHON="
    where py >nul 2>nul && set "PYTHON=py -3"
    if not defined PYTHON (
        where python >nul 2>nul && set "PYTHON=python"
    )
    if not defined PYTHON (
        echo [ERROR] Python 3.10+ not found. Install it from https://www.python.org and retry.
        exit /b 1
    )
    echo [INFO] Creating virtual environment in %VENV% ...
    !PYTHON! -m venv "%VENV%" || exit /b 1
)
set "PY=%VENV%\Scripts\python.exe"
echo [INFO] Using virtual environment: %VENV%

REM -- Install requirements if anything is missing -----------------------
"%PY%" -c "import streamlit, pygwalker, plotly, pandas, openpyxl, streamlit_sortables" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing requirements into %VENV% ^(first run takes a minute^) ...
    REM venvs created by tools such as uv have no pip - bootstrap it first
    "%PY%" -m pip --version >nul 2>nul || "%PY%" -m ensurepip --upgrade >nul 2>nul
    "%PY%" -m pip install --quiet --upgrade pip
    "%PY%" -m pip install --quiet -r requirements.txt || exit /b 1
)

REM Free the port if an earlier instance (or Amplify's embedded Jira Insights) still holds it.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [INFO] Port %PORT% in use ^(PID %%p^) - stopping it ...
    taskkill /PID %%p /F >nul 2>nul
)

echo [INFO] Jira Report Builder - http://localhost:%PORT%   (Ctrl+C to stop)
"%PY%" -m streamlit run app.py --server.port %PORT%
