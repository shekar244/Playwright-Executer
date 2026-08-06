@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  Playwright Test Executor — Windows Launcher
::  Double-click this file to start the UI.
::  Opens automatically in your default browser at http://localhost:7777
:: ============================================================

set "EXECUTER_ROOT=%~dp0"
if "%EXECUTER_ROOT:~-1%"=="\" set "EXECUTER_ROOT=%EXECUTER_ROOT:~0,-1%"

echo.
echo ============================================================
echo   Playwright Test Executor
echo ============================================================
echo   Launcher root : %EXECUTER_ROOT%
echo.

:: ----------------------------------------------------------
:: Locate Python
:: ----------------------------------------------------------

:: 1. Executer project venv
set "PYTHON=%EXECUTER_ROOT%\venv\Scripts\python.exe"
if exist "%PYTHON%" (
    echo [INFO] Using Executer venv Python: %PYTHON%
    goto :check_flask
)

:: 2. py launcher (Windows Python Launcher — tries newest Python first)
where py >nul 2>&1
if %errorlevel% == 0 (
    set "PYTHON=py"
    echo [INFO] Using Windows py launcher
    goto :check_flask
)

:: 3. python3
where python3 >nul 2>&1
if %errorlevel% == 0 (
    set "PYTHON=python3"
    echo [INFO] Using python3
    goto :check_flask
)

:: 4. python
where python >nul 2>&1
if %errorlevel% == 0 (
    set "PYTHON=python"
    echo [INFO] Using python
    goto :check_flask
)

echo [ERROR] Python not found.
echo         Install Python 3.11+ from https://python.org and ensure it is on PATH.
pause
exit /b 1

:check_flask
:: ----------------------------------------------------------
:: Ensure Flask is installed
:: ----------------------------------------------------------
"%PYTHON%" -c "import flask" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Flask not found — installing...
    "%PYTHON%" -m pip install flask --quiet
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install Flask.
        echo         Run manually:  pip install flask
        pause
        exit /b 1
    )
)

:: ----------------------------------------------------------
:: Add hosts entry for playwright-executor (once)
:: ----------------------------------------------------------
findstr /c:"playwright-executor" C:\Windows\System32\drivers\etc\hosts >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Adding 'playwright-executor' to hosts file...
    echo 127.0.0.1  playwright-executor>> C:\Windows\System32\drivers\etc\hosts 2>nul
    if %errorlevel% neq 0 (
        echo [WARN] Could not update hosts file. Right-click launch-ui.bat and choose
        echo        "Run as administrator" to enable the playwright-executor URL.
        echo        Falling back to http://localhost:7777
    ) else (
        echo [INFO] Hosts entry added.
    )
)

:: ----------------------------------------------------------
:: Launch the web server
:: ----------------------------------------------------------
cd /d "%EXECUTER_ROOT%"

echo [INFO] Starting server at http://playwright-executor:7777
echo [INFO] Your browser will open automatically.
echo [INFO] Press Ctrl+C to stop.
echo.
"%PYTHON%" server.py

set "EXIT_CODE=%errorlevel%"
if %EXIT_CODE% neq 0 (
    echo.
    echo [ERROR] Server exited with code %EXIT_CODE%.
    pause
)

endlocal
exit /b %EXIT_CODE%
