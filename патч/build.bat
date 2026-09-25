@echo off
chcp 65001 >nul
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat" x86

cd /d "%~dp0"

cl /utf-8 /O2 /LD /W3 /GS- /Fe:dinput8.dll dinput8_proxy.c /link /DEF:dinput8.def /MACHINE:X86 kernel32.lib user32.lib

if errorlevel 1 goto fail

echo.
echo === ZIBRANO ===
dir dinput8.dll
echo Skopiyuy dinput8.dll u teku z MarySkelter.exe
pause
exit /b 0

:fail
echo.
echo === NE ZIBRALOSYA ===
pause
exit /b 1
