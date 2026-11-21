@echo off
chcp 65001 >nul
title PUNTO VIVO FRESH
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Falta el entorno de Python. Ejecuta primero setup.cmd
  pause
  exit /b 1
)
set "PYTHONPATH=%~dp0src;%~dp0"
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8790/bienvenida"
".venv\Scripts\python.exe" -m punto_vivo
pause
