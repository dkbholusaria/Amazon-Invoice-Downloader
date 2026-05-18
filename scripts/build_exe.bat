@echo off
REM ============================================================
REM  Build Script — Amazon Invoice Downloader
REM  Creates a single-file EXE using PyInstaller
REM ============================================================

echo.
cd /d "%~dp0.."
echo ====================================================
echo   Building Amazon Invoice Downloader ...
echo ====================================================
echo.

REM Execute build.py using python
python scripts\build.py

echo.
pause
