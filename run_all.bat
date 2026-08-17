@echo off
color 0B
echo =======================================================
echo ASSAM FLOOD EARLY WARNING SYSTEM - ONE CLICK LAUNCHER
echo =======================================================
echo.

set "PYTHON_PATH=python"

echo [1/3] Installing/verifying dependencies...
"%PYTHON_PATH%" -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b %errorlevel%
)
echo.

echo [2/3] Running the full data pipeline (this takes 1-2 minutes)...
echo Sit back and relax while the satellite data is processed!
%PYTHON_PATH% run_full_pipeline.py
if %errorlevel% neq 0 (
    echo [ERROR] Pipeline execution failed.
    pause
    exit /b %errorlevel%
)
echo.

echo [3/3] Starting web dashboard...
:: Launch server.js using node (zero-dependency static server)
start "Assam Flood Dashboard Server" cmd /c "node server.js"
echo Waiting for server to start...
timeout /t 3 >nul

:: Open the default web browser to the dashboard
start http://localhost:8080

echo.
echo =======================================================
echo All done! The dashboard should now be open in your 
echo web browser.
echo.
echo NOTE: A separate black window is running the Node.js web server.
echo Keep it open while you are viewing the dashboard.
echo =======================================================
pause
