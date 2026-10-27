@echo off
rem Zapusk proekta "Kontrol remonta" odnim nazhatiem
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
pause
