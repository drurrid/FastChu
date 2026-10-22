@echo off
REM FastChu launcher (double-click friendly).
REM Prefer PowerShell 7 (pwsh) because it reads UTF-8 without BOM correctly;
REM fall back to Windows PowerShell 5.1 (the .ps1 is saved with a BOM for it).
cd /d "%~dp0"

where pwsh >nul 2>nul
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-bot.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-bot.ps1" %*
)

echo.
echo Bot stopped. Press any key to close this window.
pause >nul
