@echo off
REM Start the HomeTrove server (API + worker in one process).
REM
REM This is the Windows equivalent of running `hometrove serve` directly.
REM Migrations and vault initialisation are NOT handled here — run scripts\init.bat
REM once after a fresh checkout (or after pulling a release that bumps the schema).

setlocal

cd /d "%~dp0\.."

if "%HOMETROVE_DATA_DIR%"=="" set "HOMETROVE_DATA_DIR=%cd%var"
if "%HOMETROVE_LOG_LEVEL%"=="" set "HOMETROVE_LOG_LEVEL=INFO"

REM Pre-flight: friendly hint if init.bat hasn't been run yet.
if not exist "%HOMETROVE_DATA_DIR%\.initialized" (
    echo [start] data dir %HOMETROVE_DATA_DIR% looks uninitialised.
    echo         run scripts\init.bat first if this is a fresh checkout.
    echo.
)

echo ============================================================
echo   HomeTrove
echo ============================================================
echo   data dir : %HOMETROVE_DATA_DIR%
echo   api      : http://localhost:8080
echo   press Ctrl-C to stop.
echo ============================================================
echo.

python -m hometrove serve

endlocal