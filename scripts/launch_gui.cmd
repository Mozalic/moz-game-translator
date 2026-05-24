@echo off
setlocal
set SCRIPT_DIR=%~dp0
set REPO_ROOT=%SCRIPT_DIR%..
set PYTHONPATH=%REPO_ROOT%\src
python -m jp_game_translator.qt_gui
if errorlevel 10 (
  echo.
  echo PySide6 is not installed. Install the GUI dependency with:
  echo   python -m pip install -r requirements-gui.txt
  echo.
  echo Legacy WinForms fallback:
  echo   scripts\launch_winforms_gui.cmd
  pause
)
endlocal
