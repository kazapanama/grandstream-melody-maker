@echo off
rem Rebuild GSRing.exe from src\. Uses the C# compiler that ships with Windows
rem (.NET Framework) - nothing to install.
setlocal
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
"%CSC%" /nologo /target:winexe /optimize+ /codepage:65001 /out:GSRing.exe /resource:src\index.html,index.html src\GSRing.cs
if errorlevel 1 (echo BUILD FAILED & exit /b 1)
echo Built GSRing.exe
