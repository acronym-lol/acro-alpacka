@echo off
REM {{PROJECT_NAME}} -- Windows launcher.
REM On Windows the shell kind picks run.bat first (see kind handler).
REM Forwards args to a PowerShell equivalent for parity with run.sh.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
