@echo off
call "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat" 10.0.26100.0
cd /d "%~dp0"
echo ----INCLUDE----
echo %INCLUDE%
echo ----LIB----
echo %LIB%
echo ----COMPILE----
cl /nologo /EHsc /W3 test.cpp /link user32.lib gdi32.lib /SUBSYSTEM:WINDOWS
echo BUILD_EXIT=%ERRORLEVEL%
