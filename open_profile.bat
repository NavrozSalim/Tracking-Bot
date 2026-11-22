@echo off
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: open_profile.bat "Amazon AU"            sign in to the Amazon purchase account
  echo        open_profile.bat "Kogan-TFS" kogan      sign in to the store's Kogan dispatch portal
  echo        open_profile.bat "Store name" bigw      sign in to the store's Big W / Woolworths MarketPlus portal
  echo The name is the purchase account name ^(Auto Purchasing^) or the store name shown in the web app.
  pause
  exit /b 1
)
python open_profile.py "%~1" %2
