@echo off
cd /d "%~dp0"
title Tracking Bot agent
echo Tracking Bot laptop agent. Keep this window open; Chrome opens here when a tracking job starts.
echo It reads Amazon tracking for purchased orders. Every step is shown here and saved in the logs folder.
:loop
python tracking_agent.py
echo.
echo ======================================================================
echo  Tracking agent stopped with exit code %errorlevel% at %date% %time%.
echo  The reason is shown just above this box (also in the logs folder).
echo  Restarting in 30 seconds. Close this window to quit.
echo ======================================================================
timeout /t 30 /nobreak >nul
goto loop
