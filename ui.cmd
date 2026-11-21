@echo off
rem Abre la interfaz sin usar el .exe (si algo falla con el acceso directo).
setlocal
cd /d "%~dp0"
set "HF_HOME=%CD%\.cache\huggingface"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "TOKENIZERS_PARALLELISM=false"
set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
if not exist ".venv\Scripts\python.exe" (
  echo Falta el entorno. Abre setup.cmd una vez.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m shorts.ui --open %*
exit /b %errorlevel%
