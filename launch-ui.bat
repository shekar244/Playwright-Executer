@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  Playwright Test Executor — Windows Launcher
::  Double-click this file to start the UI.
:: ============================================================

:: Resolve the directory containing this script (Playwright-Executer root)
set "EXECUTER_ROOT=%~dp0"
:: Remove trailing backslash
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

:: 1. Try a venv inside the Executer project itself
set "PYTHON=%EXECUTER_ROOT%\venv\Scripts\python.exe"
if exist "%PYTHON%" (
    echo [INFO] Using Executer venv Python: %PYTHON%
    goto :launch
)

:: 2. Try the framework's venv (sibling folder, typical layout)
set "FRAMEWORK_ROOT=%EXECUTER_ROOT%\..\p13n-marketing-experiences-qa-automation"
set "PYTHON=%FRAMEWORK_ROOT%\venv\Scripts\python.exe"
if exist "%PYTHON%" (
    echo [INFO] Using framework venv Python: %PYTHON%
    goto :launch
)

:: 3. Fall back to system Python on PATH
where python >nul 2>&1
if %errorlevel% == 0 (
    set "PYTHON=python"
    echo [INFO] Using system Python
    goto :launch
)

where python3 >nul 2>&1
if %errorlevel% == 0 (
    set "PYTHON=python3"
    echo [INFO] Using system python3
    goto :launch
)

echo [ERROR] Python not found.
echo         Install Python 3.9+ and ensure it is on your PATH, then retry.
pause
exit /b 1

:launch
:: ----------------------------------------------------------
:: Start the UI from the Executer root
:: ----------------------------------------------------------
cd /d "%EXECUTER_ROOT%"

echo [INFO] Starting UI ...
echo.
"%PYTHON%" -m ui_launcher

set "EXIT_CODE=%errorlevel%"
if %EXIT_CODE% neq 0 (
    echo.
    echo [ERROR] The UI exited with code %EXIT_CODE%.
    echo         Check the message above for details.
    pause
)

endlocal
exit /b %EXIT_CODE%
