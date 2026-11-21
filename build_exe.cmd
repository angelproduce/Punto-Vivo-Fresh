@echo off
rem Crea "ANGEL PRODUCE IA.exe" (una sola vez, o cuando cambies el código).
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1"
pause
