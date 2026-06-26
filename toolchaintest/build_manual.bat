@echo off
setlocal
set VC=C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC\14.51.36231
set SDK=C:\Program Files (x86)\Windows Kits\10
set SDKVER=10.0.26100.0

set INCLUDE=%VC%\include;%SDK%\Include\%SDKVER%\ucrt;%SDK%\Include\%SDKVER%\um;%SDK%\Include\%SDKVER%\shared
set LIB=%VC%\lib\x64;%SDK%\Lib\%SDKVER%\ucrt\x64;%SDK%\Lib\%SDKVER%\um\x64
set PATH=%VC%\bin\Hostx64\x64;%PATH%

cd /d "%~dp0"
echo ----COMPILE----
cl /nologo /EHsc /W3 hello.cpp
echo BUILD_EXIT=%ERRORLEVEL%
