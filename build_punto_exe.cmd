@echo off
rem Crea "PUNTO VIVO FRESH.exe" (una sola vez, o cuando cambies el código).
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_punto_exe.ps1"
pause
