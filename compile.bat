@echo off
rem Usage: compile.bat path\to\source.cpp   ->   builds source.exe next to it
rem Bypasses vcvars64.bat (broken SDK registry detection on this machine)
rem by pointing INCLUDE/LIB/PATH straight at the installed MSVC + Windows SDK.
setlocal
set VC=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.51.36231
set SDK=C:\Program Files (x86)\Windows Kits\10
set SDKVER=10.0.26100.0

set INCLUDE=%VC%\include;%SDK%\Include\%SDKVER%\ucrt;%SDK%\Include\%SDKVER%\um;%SDK%\Include\%SDKVER%\shared;%SDK%\Include\%SDKVER%\winrt
set LIB=%VC%\lib\x64;%SDK%\Lib\%SDKVER%\ucrt\x64;%SDK%\Lib\%SDKVER%\um\x64
set PATH=%VC%\bin\Hostx64\x64;%PATH%

cl /nologo /EHsc /W3 /Zi /Fe:"%~dpn1.exe" "%~1"
exit /b %ERRORLEVEL%
