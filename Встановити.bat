@echo off
rem Only ASCII in this file: cmd.exe misreads UTF-8 Cyrillic in .bat files.
echo Installing required libraries...
python -m pip install --upgrade pip
python -m pip install openpyxl UnityPy Pillow texture2ddecoder etcpak sv-ttk
echo.
if errorlevel 1 (
  echo Something went wrong. The most common reason is that Python is not installed,
  echo or "Add Python to PATH" wasn't checked during installation.
) else (
  echo Done. You can now run Pereklad.pyw
)
pause
