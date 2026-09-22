@echo off
setlocal
cd /d "%~dp0"

echo Building FlipClock.exe ...
python -m PyInstaller FlipClock.spec --noconfirm --clean
if errorlevel 1 (
    echo Build failed.
    exit /b 1
)

echo.
echo Done. Run: dist\FlipClock\FlipClock.exe
endlocal
