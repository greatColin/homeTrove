@echo off
REM HomeTrove first-run init: migrations + vault password setup.
REM
REM Run once before the first "start.bat". Safe to re-run on upgrades.
REM
REM Vault password resolution order:
REM   1. %HOMETROVE_VAULT_PASSWORD% (non-empty)  -> used directly
REM   2. Interactive prompt                       -> caller types it twice
REM   3. Skipped (just prints a reminder)         -> vault stays uninitialized
REM
REM Migrations run unconditionally (alembic upgrade head is idempotent).

setlocal enabledelayedexpansion

cd /d "%~dp0\.."

if "%HOMETROVE_DATA_DIR%"=="" set "HOMETROVE_DATA_DIR=%cd%var"
if "%HOMETROVE_LOG_LEVEL%"=="" set "HOMETROVE_LOG_LEVEL=INFO"

echo.
echo ============================================================
echo   HomeTrove first-run init
echo ============================================================
echo   data dir : %HOMETROVE_DATA_DIR%
echo.

REM ---- 1. migrations --------------------------------------------------------
echo [1/2] Running alembic migrations...
python -m hometrove migrate
if errorlevel 1 (
    echo [init] migration failed, aborting.
    exit /b 1
)
echo.

REM ---- 2. vault setup -------------------------------------------------------
echo [2/2] Vault setup
echo.
echo The vault is enabled by default. New uploads are stored encrypted when the
echo vault is unlocked. You need a master password (>= 12 chars).
echo.
echo Choose how to provide it:
echo   [E]nter now  (interactive prompt)
echo   [S]kip       (set HOMETROVE_VAULT_PASSWORD later, or call /api/vault/setup)
echo.
set "CHOICE="
set /p "CHOICE=Choice [E/S]: "
if /i "%CHOICE%"=="S" goto :vault_skip
if /i "%CHOICE%"=="s" goto :vault_skip

if not "%HOMETROVE_VAULT_PASSWORD%"=="" goto :have_password

:prompt_password
set "PW1="
set "PW2="
set /p "PW1=Master password (min 12 chars): "
set /p "PW2=Confirm password            : "
if not "%PW1%"=="%PW2%" (
    echo [init] passwords do not match, try again.
    echo.
    goto :prompt_password
)
if not defined PW1 goto :vault_skip
set "HOMETROVE_VAULT_PASSWORD=%PW1%"

:have_password

REM Pipe the password to the helper so it never appears in argv or process
REM listings. The Python script keeps it in a local variable only.
set "PW_FILE=%TEMP%\hometrove-pw-%RANDOM%.txt"
< nul set /p "=%HOMETROVE_VAULT_PASSWORD%" > "%PW_FILE%"
echo. >> "%PW_FILE%"

python "%~dp0_init_vault.py" < "%PW_FILE%"
set "SETUP_RC=%ERRORLEVEL%"

REM Overwrite then delete the temp file. NTFS plain delete is sufficient for
REM this use case (no forensic-recovery requirement).
> "%PW_FILE%" echo.
del "%PW_FILE%" 2>nul
set "PW_FILE="

REM Clear password from env so it doesn't leak into any child process.
set "HOMETROVE_VAULT_PASSWORD="
set "PW1="
set "PW2="

if not "%SETUP_RC%"=="0" (
    echo [init] vault setup failed, aborting.
    exit /b 1
)

REM Mark data dir as initialised so start.bat's preflight is silent next time.
if not exist "%HOMETROVE_DATA_DIR%" mkdir "%HOMETROVE_DATA_DIR%"
echo. > "%HOMETROVE_DATA_DIR%\.initialized" 2>nul

goto :done

:vault_skip
echo.
echo [init] vault setup skipped. Run with HOMETROVE_VAULT_PASSWORD set, or call
echo        POST /api/vault/setup later before uploading.

:done
echo.
echo ============================================================
echo   init complete
echo ============================================================
echo Next: run start.bat to launch API + worker.
echo.

endlocal