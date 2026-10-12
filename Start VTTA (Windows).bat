@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem ==========================================================================
rem  VTTA - Voice to Text Assistant : launcher for WINDOWS
rem
rem  Double-click this file to start VTTA.
rem  The first start sets up a private Python environment inside the VTTA
rem  folder (needs internet once, takes a few minutes). Later starts open
rem  VTTA straight away. Nothing is installed outside the VTTA folder.
rem
rem  Options:  "Start VTTA (Windows).bat" menu    - options and troubleshooting
rem            "Start VTTA (Windows).bat" debug   - start and show messages
rem            "Start VTTA (Windows).bat" setup   - repair (reinstall components)
rem  ("Troubleshoot VTTA (Windows).bat" in the app folder opens the menu.)
rem ==========================================================================
rem Release downloads keep the program in an "app" subfolder; a source checkout keeps it here.
set "LAUNCHDIR=%~dp0"
set "APPDIR=%~dp0"
if exist "%~dp0app\Voice_To_Text_Assistant.py" set "APPDIR=%~dp0app\"
cd /d "%APPDIR%"
set "VENV=%APPDIR%.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "VPYW=%VENV%\Scripts\pythonw.exe"
set "MODE=%~1"

rem In a full source download, hide the Mac/Linux launchers so only this one shows.
if exist "%LAUNCHDIR%Start VTTA (Mac).command" attrib +h "%LAUNCHDIR%Start VTTA (Mac).command" >nul 2>&1
if exist "%LAUNCHDIR%Start VTTA (Linux).sh" attrib +h "%LAUNCHDIR%Start VTTA (Linux).sh" >nul 2>&1

if /i "%MODE%"=="menu" goto menu
if /i "%MODE%"=="setup" goto setup

:check
if not exist "%VPY%" goto setup
"%VPY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto setup
fc /b "requirements.txt" "%VENV%\vtta-requirements.txt" >nul 2>&1
if errorlevel 1 goto install
goto run

:setup
title VTTA - first-time setup
echo.
echo   VTTA setup
echo   ----------
echo   This creates a private Python environment inside the VTTA folder and
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
if not exist "%VENV%\vtta-shortcut-asked" call :shortcut_ask
goto run

:run
if /i "%MODE%"=="debug" goto run_debug
start "" "%VPYW%" "%APPDIR%Voice_To_Text_Assistant.py"
exit /b 0

:run_debug
title VTTA - running with messages
echo.
echo   VTTA is running. Its messages appear below; close VTTA to return here.
echo.
"%VPY%" "%APPDIR%Voice_To_Text_Assistant.py"
echo.
pause
exit /b

rem ---------------------------------------------------------------- menu ---
:menu
title VTTA - options
cls
echo.
echo   VTTA - options and troubleshooting
echo   ----------------------------------
echo     1   Start VTTA
echo     2   Start VTTA and show its messages   (use this if VTTA won't open)
echo     3   Repair VTTA                        (reinstall its components)
echo     4   Create a desktop shortcut
echo     5   Open the VTTA folder
echo     6   Free disk space                    (remove the ~1 GB of components;
echo                                             they are set up again next start)
echo     0   Close
echo.
choice /c 1234560 /n /m "  Type a number: "
set "PICK=%errorlevel%"
if "%PICK%"=="1" goto menu_start
if "%PICK%"=="2" goto menu_debug
if "%PICK%"=="3" goto setup
if "%PICK%"=="4" goto menu_shortcut
if "%PICK%"=="5" goto menu_folder
if "%PICK%"=="6" goto menu_free
exit /b 0

:menu_start
set "MODE="
goto check

:menu_debug
set "MODE=debug"
goto check

:menu_shortcut
call :shortcut_make
pause
goto menu

:menu_folder
start "" explorer "%LAUNCHDIR%"
goto menu

:menu_free
echo.
echo   This removes VTTA's private Python environment (about 1 GB). Your settings,
echo   history and downloaded speech models are kept. Close VTTA first.
choice /c YN /n /m "  Remove it now? [Y/N] "
if errorlevel 2 goto menu
if exist "%VENV%" rmdir /s /q "%VENV%"
if exist "%VENV%" echo   Some files are in use - close VTTA and try again.
if not exist "%VENV%" echo   Removed. The next start sets it up again.
pause
goto menu

rem ------------------------------------------------------------- helpers ---
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

:shortcut_ask
echo ok> "%VENV%\vtta-shortcut-asked"
echo.
choice /c YN /n /m "  Create a desktop shortcut for VTTA? [Y/N] "
if errorlevel 2 goto :eof
call :shortcut_make
goto :eof

:shortcut_make
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'VTTA.lnk')); $s.TargetPath=$env:VPYW; $s.Arguments=$q+$env:APPDIR+'Voice_To_Text_Assistant.py'+$q; $s.WorkingDirectory=$env:APPDIR; $s.IconLocation=$env:APPDIR+'assets\vtta.ico'; $s.Description='VTTA - Voice to Text Assistant'; $s.Save()"
if errorlevel 1 goto shortcut_failed
echo   Shortcut "VTTA" created on your desktop.
goto :eof
:shortcut_failed
echo   Could not create the shortcut. You can always start VTTA with this file.
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
echo   When the Python install has finished, double-click "Start VTTA (Windows).bat" again.
pause
exit /b 1

:no_python_manual
echo   Install Python 3.12 from python.org - in the installer tick
echo   "Add python.exe to PATH" - then double-click "Start VTTA (Windows).bat" again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:fail
echo.
echo   Setup did not finish. The messages above say why - most often the internet
echo   connection dropped. Double-click "Start VTTA (Windows).bat" to try again.
pause
exit /b 1
