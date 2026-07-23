@echo off
setlocal
if /i "%~1"=="log" goto dev_logs
if /i "%~1"=="logs" goto dev_logs
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cursor-pulse.ps1" %*
exit /b %ERRORLEVEL%

:dev_logs
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cursor-pulse.ps1" %*
  exit /b %ERRORLEVEL%
)
"%PY%" -m pulse.dev logs %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%
