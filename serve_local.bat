@echo off
REM Start the Hub for the office. Double-click, or run from a terminal.
REM Leave this window open: closing it stops the server.
cd /d "%~dp0"
".venv\Scripts\python.exe" serve_local.py %*
pause
