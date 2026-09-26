@echo off
setlocal
cd /d "%~dp0"
echo === Syncify build ===

where python >nul 2>nul || (echo Python not found. Install Python 3.12 or 3.13 from python.org and tick "Add to PATH". & pause & exit /b 1)

if not exist syncify\oauth_client.json (
  echo.
  echo WARNING: syncify\oauth_client.json not found. The exe will build, but Google sign-in and sync
  echo          will be off. See README, "One-time Google setup".
  echo.
  set ADD_OAUTH=
) else (
  set ADD_OAUTH=--add-data "syncify\oauth_client.json;syncify"
)

if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || (echo venv failed & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements-dev.txt || (echo pip install failed & pause & exit /b 1)

echo Building Syncify.exe...
pyinstaller --noconfirm --onefile --windowed --name Syncify --icon assets\syncify.ico --add-data "ui;ui" %ADD_OAUTH% ^
  --hidden-import keyring.backends.Windows run.py || (echo Build failed & pause & exit /b 1)

echo.
echo Done: dist\Syncify.exe  (put it anywhere and double-click it)
pause
