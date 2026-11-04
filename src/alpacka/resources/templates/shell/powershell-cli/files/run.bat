@echo off
REM {{PROJECT_NAME}} -- ALPACKA shell-kind entry point on Windows.
REM Forwards args to run.ps1 via PowerShell.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
