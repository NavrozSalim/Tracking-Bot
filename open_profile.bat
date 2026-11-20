@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: open_profile.bat "Amazon AU"
  echo The name is the purchase account name shown on the store's Auto Purchasing step.
  pause
  exit /b 1
)
python open_profile.py "%~1"
