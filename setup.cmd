@echo off
rem Instalación portable. Se ejecuta una sola vez.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo Algo fallo. Lee el mensaje de arriba.
  pause
)
pause
