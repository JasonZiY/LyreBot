@echo off
rem Drag .mid files onto this file to arrange them into the music folder.
setlocal
set "PYTHONPATH=%~dp0"
if "%~1"=="" goto usage
where python >nul 2>nul
if not errorlevel 1 goto python
where py >nul 2>nul
if not errorlevel 1 goto py
echo Python 3.11 or newer is needed. See README.md in this folder for help.
goto end
:python
python -m lyrebot.arrange %*
goto end
:py
py -3 -m lyrebot.arrange %*
goto end
:usage
echo Drag one or more .mid files onto this file.
echo The arranged scores are saved into the music folder.
:end
echo.
pause
