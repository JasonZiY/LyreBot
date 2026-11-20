@echo off
setlocal
cd /d "%~dp0"
if exist "python\python.exe" goto bundled
where python >nul 2>nul
if not errorlevel 1 goto python
where py >nul 2>nul
if not errorlevel 1 goto py
echo Python 3.11 or newer is needed to run LyreBot,
echo or the portable LyreBot, which brings its own.
echo See README.md in this folder for help.
pause
exit /b 1
:bundled
"python\python.exe" -m lyrebot
goto done
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
