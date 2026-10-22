@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if not errorlevel 1 goto python
where py >nul 2>nul
if not errorlevel 1 goto py
echo Python 3.11 or newer is needed to run LyreBot.
echo See README.md in this folder for help.
pause
exit /b 1
:python
python -m lyrebot
goto done
:py
py -3 -m lyrebot
:done
if errorlevel 1 (
  echo.
  echo LyreBot could not start. Keep this window open and share the error above.
  pause
)
