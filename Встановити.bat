@echo off
rem Only ASCII in this file: cmd.exe misreads UTF-8 Cyrillic in .bat files.
echo Installing required libraries...
python -m pip install --upgrade pip
python -m pip install openpyxl UnityPy Pillow texture2ddecoder etcpak sv-ttk fonttools numpy
rem anthropic (machine translation via Claude) is optional: the machine translation window installs it on demand
rem PySide6 (Qt) draws the program window since 3.1; separate line so a failure here does not block the rest
python -m pip install PySide6
echo.
if errorlevel 1 (
  echo Something went wrong. The most common reason is that Python is not installed,
  echo or "Add Python to PATH" wasn't checked during installation.
) else (
  echo Done. You can now run Pereklad.pyw
)
pause
