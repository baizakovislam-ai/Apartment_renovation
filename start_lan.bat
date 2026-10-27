@echo off
rem Zapusk proekta "Kontrol remonta" odnim nazhatiem v lokalnoy seti
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Lan %*
pause
