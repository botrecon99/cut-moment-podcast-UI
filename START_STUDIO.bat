@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if errorlevel 1 goto :BAD_FOLDER
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONNOUSERSITE=1"
set "PYTHONHOME="
set "PYTHONPATH="
set "PATH=%~dp0_python311\Scripts;%~dp0_python311;%~dp0tools\ffmpeg\bin;%~dp0;%PATH%"
if not exist "%~dp0_python311\python.exe" goto :MISSING
"%~dp0_python311\python.exe" "%~dp0studio_server.py"
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo [ERROR] Studio exited with code %RC%.
pause
exit /b %RC%
:MISSING
echo [ERROR] This folder has no _python311\python.exe.
echo Run INSTALL_NEW_PC.bat INSIDE this LaughDetectionV2 folder.
pause
exit /b 1
:BAD_FOLDER
echo [ERROR] Could not access the LaughDetectionV2 folder.
pause
exit /b 1
