@echo off
setlocal
cd /d "%~dp0" || goto error

set "OGP_PYTHON=.venv\Scripts\python.exe"
if not exist "%OGP_PYTHON%" (
    py -3.11 -m venv .venv || goto error
)

if not exist ".venv\ogp-installed" (
    "%OGP_PYTHON%" -m pip install -e . || goto error
    type nul > ".venv\ogp-installed" || goto error
)

"%OGP_PYTHON%" -m open_garden_planner %* || goto error
exit /b 0

:error
echo Zagon Open Garden Planner ni uspel. Preveri sporocila zgoraj.
if not defined OGP_NO_PAUSE pause
exit /b 1
