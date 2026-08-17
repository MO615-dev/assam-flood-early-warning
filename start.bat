@echo off
title Assam Flood Digital Twin Server Launcher
echo ============================================================
echo      ASSAM FLOOD DIGITAL TWIN - DUAL SERVER LAUNCHER
echo ============================================================
echo.
echo Searching for python installation...
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in the PATH.
    echo Please install Python 3.10+ from python.org before running.
    echo.
    pause
    exit /b
)

echo Starting main server on port 8080 and mobile monitor on 8081...
echo.
python webapp/server.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] The server exited with an error. 
    echo Check python output logs or try running 'python -m pip install -r requirements.txt' if libraries are missing.
    echo.
)
pause
