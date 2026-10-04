@echo off
REM ============================================================
REM  Jira Report Builder - Windows launcher
REM  Run:  run.bat            (set PORT=8600 first to change port)
REM  Virtual environment: VENV_DIR if set, else .venv or venv in this folder,
REM  else .venv or venv in the parent folder; otherwise .\venv is created.
REM  Missing requirements are installed; errors pause so you can read them.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8501

REM -- Pick the virtual environment ---------------------------------------
set "VENV="
if defined VENV_DIR set "VENV=%VENV_DIR%"
for %%v in (".venv" "venv") do (
    if not defined VENV if exist "%%~v\Scripts\python.exe" set "VENV=%%~v"
)
if not defined VENV set "VENV=venv"

if not exist "%VENV%\Scripts\python.exe" (
    set "PYTHON="
    where py >nul 2>nul && set "PYTHON=py -3"
    if not defined PYTHON (
        where python >nul 2>nul && set "PYTHON=python"
    )
    if not defined PYTHON goto :no_python
    echo [INFO] Creating virtual environment in %VENV% ...
    !PYTHON! -m venv "%VENV%"
    if errorlevel 1 goto :venv_failed
)
set "PY=%VENV%\Scripts\python.exe"
echo [INFO] Using virtual environment: %VENV%

REM -- Install requirements only if something is missing -----------------
"%PY%" -c "import streamlit, pygwalker, plotly, pandas, openpyxl, streamlit_sortables" >nul 2>nul
if errorlevel 1 (
    echo [INFO] Installing missing requirements into %VENV% ...
    "%PY%" -m pip --version >nul 2>nul || "%PY%" -m ensurepip --upgrade >nul 2>nul
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 goto :install_failed
)

REM Free the port if an earlier instance (or Amplify's embedded Jira Insights) still holds it.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
    echo [INFO] Port %PORT% in use ^(PID %%p^) - stopping it ...
    taskkill /PID %%p /F >nul 2>nul
)

echo [INFO] Jira Report Builder - http://localhost:%PORT%   (Ctrl+C to stop)
"%PY%" -m streamlit run app.py --server.port %PORT%
if errorlevel 1 (
    echo.
    echo [ERROR] Jira Report Builder stopped with an error - see the messages above.
    pause
    exit /b 1
)
exit /b 0

:no_python
echo [ERROR] Python 3.10+ not found. Install it from https://www.python.org and run run.bat again.
pause
exit /b 1

:venv_failed
echo [ERROR] Could not create the virtual environment in %VENV%.
pause
exit /b 1

:install_failed
echo.
echo [ERROR] Could not install the requirements into %VENV%.
echo         On a company network, point pip at your package mirror once, e.g.
echo           "%PY%" -m pip config set global.index-url https://YOUR-MIRROR/api/pypi/pypi/simple
echo         then run run.bat again - or install manually:
echo           "%PY%" -m pip install -r requirements.txt
pause
exit /b 1
