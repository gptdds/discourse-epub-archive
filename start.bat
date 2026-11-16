@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :error
)
start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0
:error
echo.
echo Setup failed. See the messages above.
pause
exit /b 1
