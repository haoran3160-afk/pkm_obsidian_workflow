@ECHO OFF
SETLOCAL

REM ── PKM Daily Automation ──────────────────────────────────────────────────
REM  Portable script: uses %~dp0 to locate itself regardless of install path.
REM  Requires Python to be available on PATH (set via system environment or
REM  virtual environment activation).
REM
REM  Setup:
REM    1. Copy .env.example to .env and fill in your values
REM    2. Run setup_task_scheduler.ps1 (Admin) to register in Task Scheduler
REM    3. Or run this .bat manually to trigger an immediate fetch

SET "SCRIPT_DIR=%~dp0"
SET "SCRIPT=%SCRIPT_DIR%main.py"
SET "LOG=%SCRIPT_DIR%fetch_cron.log"

REM Prefer Python from PATH; fall back to common Anaconda locations
WHERE python >NUL 2>&1
IF %ERRORLEVEL% NEQ 0 (
    IF EXIST "D:\anacoda\python.exe" (
        SET "PYTHON=D:\anacoda\python.exe"
    ) ELSE (
        ECHO [ERROR] Python not found on PATH. Please activate your virtual environment.
        EXIT /B 1
    )
) ELSE (
    SET "PYTHON=python"
)

ECHO. >> "%LOG%"
ECHO ============================================================ >> "%LOG%"
ECHO [%DATE% %TIME%] PKM Daily Auto-Fetch Started >> "%LOG%"
ECHO ============================================================ >> "%LOG%"

"%PYTHON%" "%SCRIPT%" --raw-only >> "%LOG%" 2>&1
SET "EXIT_CODE=%ERRORLEVEL%"

IF "%EXIT_CODE%"=="0" (
    ECHO [%DATE% %TIME%] Fetch completed successfully. >> "%LOG%"
    POWERSHELL -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('PKM Raw Feeds Ready!`n`nOpen Antigravity and run /pkm-daily-digest to curate today''s digest.', 'PKM AutoFetch Done', 'OK', 'Information')"
) ELSE (
    ECHO [%DATE% %TIME%] Fetch FAILED with exit code %EXIT_CODE%. >> "%LOG%"
    POWERSHELL -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('PKM fetch failed. Check fetch_cron.log for details.', 'PKM AutoFetch Error', 'OK', 'Error')"
)

ENDLOCAL
EXIT /B %EXIT_CODE%