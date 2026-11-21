@echo off
rem Uso:  run.cmd doctor        run.cmd run -n 1        run.cmd ui
setlocal
cd /d "%~dp0"
set "HF_HOME=%CD%\.cache\huggingface"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
set "TOKENIZERS_PARALLELISM=false"
set "PYTHONPATH=%CD%\src;%CD%\.venv\Lib\site-packages;%PYTHONPATH%"
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Falta el entorno. Abre setup.cmd una vez y espera a que termine.
  echo.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m shorts %*
exit /b %errorlevel%
