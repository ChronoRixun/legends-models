@echo off
setlocal
set "MUA_VS_ROOT="
set "MUA_VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%MUA_VSWHERE%" (
  echo Visual Studio C++ Build Tools and vswhere are required.
  exit /b 2
)
for /f "usebackq delims=" %%I in (`"%MUA_VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "MUA_VS_ROOT=%%I"
if not defined MUA_VS_ROOT exit /b 3
set "PATH=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer;%PATH%"
call "%MUA_VS_ROOT%\VC\Auxiliary\Build\vcvars32.bat" >nul
if errorlevel 1 exit /b 4
cl /nologo /O2 /EHsc /MT tools\sample_enbaya.cpp /Fe:tools\sample_enbaya.exe /Fo:tools\sample_enbaya.obj
exit /b %errorlevel%
