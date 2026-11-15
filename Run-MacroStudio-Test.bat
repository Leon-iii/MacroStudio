@echo off
setlocal
pushd "%~dp0macro-studio" || exit /b 1

if not exist ".venv\Scripts\python.exe" (
    echo Test environment missing: macro-studio\.venv\Scripts\python.exe
    pause
    popd
    exit /b 1
)

set "PYTHONPATH=%CD%\src"
".venv\Scripts\python.exe" -m macro_studio.gui
set "studio_exit_code=%ERRORLEVEL%"
if not "%studio_exit_code%"=="0" pause
popd
exit /b %studio_exit_code%
