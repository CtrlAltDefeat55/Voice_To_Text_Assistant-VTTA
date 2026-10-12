@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem ==========================================================================
rem  VTTA - Voice to Text Assistant : Windows launcher
rem  Double-click to start VTTA.
rem  The first run sets up a private Python environment in the ".venv" folder
rem  next to this file (needs internet once, takes a few minutes). Later runs
rem  start VTTA straight away. Everything stays inside this folder.
rem
rem    "Start VTTA.bat" debug   - run with this console open to see errors
rem    "Start VTTA.bat" setup   - repeat the setup (repair)
rem ==========================================================================
cd /d "%~dp0"
set "APPDIR=%~dp0"
set "VENV=%APPDIR%.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "VPYW=%VENV%\Scripts\pythonw.exe"

if /i "%~1"=="setup" goto setup
if not exist "%VPY%" goto setup
"%VPY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto setup
fc /b "requirements.txt" "%VENV%\vtta-requirements.txt" >nul 2>&1
if errorlevel 1 goto install
goto run

:setup
title VTTA - first-time setup
echo.
echo   VTTA first-time setup
echo   ---------------------
echo   This creates a private Python environment inside this folder and
echo   downloads the components VTTA needs (about 1 GB, one time only).
echo.
call :find_python
if not defined PY goto no_python
if exist "%VENV%" rmdir /s /q "%VENV%"
echo   Creating the environment with: %PY%
%PY% -m venv "%VENV%"
if errorlevel 1 goto fail

:install
title VTTA - installing components
echo.
echo   Installing components. This can take several minutes...
echo.
"%VPY%" -m pip install --disable-pip-version-check --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
"%VPY%" -c "import tkinter, numpy, PIL, pyaudio, whisper"
if errorlevel 1 goto fail
copy /y "requirements.txt" "%VENV%\vtta-requirements.txt" >nul
echo.
echo   Setup complete.
call :shortcut
goto run

:run
if /i "%~1"=="debug" goto run_debug
start "" "%VPYW%" "%APPDIR%Voice_To_Text_Assistant.py"
exit /b 0

:run_debug
"%VPY%" "%APPDIR%Voice_To_Text_Assistant.py"
pause
exit /b

:find_python
rem Prefer versions that all of VTTA's components support.
set "PY="
for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
        py -%%V -c "import tkinter" >nul 2>&1 && set "PY=py -%%V"
    )
)
if defined PY goto :eof
python -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
goto :eof

:shortcut
echo.
choice /c YN /n /m "  Create a desktop shortcut for VTTA? [Y/N] "
if errorlevel 2 goto :eof
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'VTTA.lnk')); $s.TargetPath=$env:VPYW; $s.Arguments=$q+$env:APPDIR+'Voice_To_Text_Assistant.py'+$q; $s.WorkingDirectory=$env:APPDIR; $s.IconLocation=$env:APPDIR+'assets\vtta.ico'; $s.Description='VTTA - Voice to Text Assistant'; $s.Save()"
if errorlevel 1 (echo   Could not create the shortcut. You can start VTTA with this file instead.) else (echo   Shortcut created on your desktop.)
goto :eof

:no_python
echo   Python 3.10 - 3.13 was not found on this computer.
echo.
where winget >nul 2>&1
if errorlevel 1 goto no_python_manual
choice /c YN /n /m "  Install Python 3.12 now (for your user account only)? [Y/N] "
if errorlevel 2 goto no_python_manual
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
echo.
echo   When the Python install has finished, double-click "Start VTTA" again.
pause
exit /b 1

:no_python_manual
echo   Install Python 3.12 from python.org - in the installer tick
echo   "Add python.exe to PATH" - then double-click "Start VTTA" again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:fail
echo.
echo   Setup did not finish. The messages above say why - most often the
echo   internet connection dropped. Double-click "Start VTTA" to try again.
pause
exit /b 1
