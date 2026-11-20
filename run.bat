@echo off
setlocal
cd /d "%~dp0" || goto error

set "OGP_PYTHON=.venv\Scripts\python.exe"
if not exist "%OGP_PYTHON%" (
    py -3.11 -m venv .venv || goto error
)

"%OGP_PYTHON%" -c "import open_garden_planner, PyQt6.QtWidgets" >nul 2>&1
if errorlevel 1 (
    "%OGP_PYTHON%" -m pip install -e . || goto error
)

"%OGP_PYTHON%" -m open_garden_planner %* || goto error
exit /b 0

:error
echo Zagon Open Garden Planner ni uspel. Preveri sporocila zgoraj.
if not defined OGP_NO_PAUSE pause
exit /b 1
