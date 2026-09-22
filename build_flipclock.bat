@echo off
cd /d "%~dp0"
".\.venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean FlipClock.spec
if errorlevel 1 exit /b %ERRORLEVEL%
if exist "dist\FlipClock" (
  if exist "FlipClock" rmdir /s /q "FlipClock"
  xcopy /e /i /y "dist\FlipClock" "FlipClock" >nul
  echo Built: FlipClock\FlipClock.exe
)
