@echo off
setlocal

REM =========================================
REM Amazon Business Invoice Downloader
REM Portable BAT Launcher
REM =========================================

REM Project folder is automatically detected from the location of this BAT file.
REM Keep this BAT file in the same folder as the Python script.

set "PROJECT_PATH=%~dp0"
set "PYTHON_EXE=python"
set "SCRIPT_FILE=amazon_download_complete_documented.py"
set "EXE_FILE=dist\AmazonInvoiceDownloader.exe"

REM Set USE_EXE=1 to run the standalone EXE (portable).
REM Set USE_EXE=0 to run the Python script (developer mode).
set "USE_EXE=0"

REM Destination mother folder where FY sub-folders will be created.

rem set "DEST_PATH=D:\Deepak.Bhholusaria\OneDrive - DAKSM\DAKSMLLP\Accounting\Expenses\Amazon"

set "DEST_PATH=D:\Deepak.Bhholusaria\OneDrive - DAKSM\Documents"

REM Supported period values:
REM current-month
REM last-month
REM current-quarter
REM current-fy
REM last-fy
REM last-12-months
REM previous-month   [alias for last-month]
REM month-to-date    [alias for current-month]

set "PERIOD=last-month"

set "PERIOD=last-12-months"

REM Set HEADED=1 to show browser window for debugging.
REM Set HEADED=0 to run browser in background.
set "HEADED=0"

set "USER_DIR=%USERPROFILE%\amazon_invoice_downloader"
if not exist "%USER_DIR%" mkdir "%USER_DIR%"
set "LOG_FILE=%USER_DIR%\run.log"

REM =========================================
REM Start
REM =========================================

cd /d "%PROJECT_PATH%"

echo ****************************************
echo *  Amazon Business Invoice Downloader  *
echo *  By CA. Deepak Bhholusaria, (c) 2026 *
echo ****************************************
echo.

echo Starting download. Please wait!
echo [%DATE% %TIME%] ======== Starting Amazon download ======== >> "%LOG_FILE%"

if "%USE_EXE%"=="1" (
    set "RUN_CMD="%EXE_FILE%""
) else (
    set "RUN_CMD="%PYTHON_EXE%" "%SCRIPT_FILE%""
)

if "%HEADED%"=="1" (
    %RUN_CMD% --no-gui --dest "%DEST_PATH%" --period "%PERIOD%" --headed >> "%LOG_FILE%" 2>&1
) else (
    %RUN_CMD% --no-gui --dest "%DEST_PATH%" --period "%PERIOD%" >> "%LOG_FILE%" 2>&1
)

set "EXITCODE=%ERRORLEVEL%"

echo [%DATE% %TIME%] ======== Finished. Exit code: %EXITCODE% ======== >> "%LOG_FILE%"

if "%EXITCODE%"=="0" (
    echo.
    echo Download completed successfully.
) else (
    echo.
    echo ERROR: Amazon download failed.
    echo Check log file:
    echo "%LOG_FILE%"
)

echo.
pause
exit /b %EXITCODE%
