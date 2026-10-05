@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ordax-agent-install.ps1" -StartNow %*
if errorlevel 1 (
  echo.
  echo Falha ao instalar o OrdaX Dev Agent.
  pause
  exit /b 1
)
echo.
echo OrdaX Dev Agent instalado e iniciado.
timeout /t 5 >nul
