@echo off
REM ============================================================
REM  Jira Report Builder - Windows launcher
REM  Run:  run.bat            (set PORT=8600 first to change port)
REM  First run creates .\venv and installs requirements.txt.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8501

if not exist "venv\Scripts\python.exe" (
    set "PYTHON="
    where py >nul 2>nul && set "PYTHON=py -3"
    if not defined PYTHON (
        where python >nul 2>nul && set "PYTHON=python"
    )
    if not defined PYTHON (
        echo [ERROR] Python 3.10+ not found. Install it from https://www.python.org and retry.
        exit /b 1
    )
    echo [INFO] Creating virtual environment ...
    !PYTHON! -m venv venv || exit /b 1
)

venv\Scripts\python.exe -c "import streamlit, pygwalker, plotly, pandas, openpyxl, streamlit_sortables" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing requirements ^(first run takes a minute^) ...
    venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    venv\Scripts\python.exe -m pip install --quiet -r requirements.txt || exit /b 1
)

REM Free the port if an earlier instance (or Amplify's embedded Jira Insights) still holds it.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [INFO] Port %PORT% in use ^(PID %%p^) - stopping it ...
    taskkill /PID %%p /F >nul 2>nul
)

echo [INFO] Jira Report Builder - http://localhost:%PORT%   (Ctrl+C to stop)
venv\Scripts\python.exe -m streamlit run app.py --server.port %PORT%
