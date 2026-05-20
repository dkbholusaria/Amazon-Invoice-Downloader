@echo off
REM ============================================================
REM  Build Script — Amazon Invoice Downloader
REM  Creates a single-file EXE using PyInstaller
REM ============================================================

echo.
echo ====================================================
echo   Building Amazon Invoice Downloader ...
echo ====================================================
echo.

REM Execute build.py using its absolute path reference
python "%~dp0build.py"

echo.
pause
