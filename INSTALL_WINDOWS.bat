@echo off
setlocal EnableExtensions
chcp 65001 >nul
TITLE Optical Table Designer v0.3 - FreeCAD 1.1 installer

echo ============================================================
echo  Optical Table Designer v0.3 - Windows installer for FreeCAD 1.1
echo ============================================================
echo.
set "SOURCE=%~dp0OpticalTableDesigner"
set "USER_DEST=%APPDATA%\FreeCAD\v1-1\Mod\OpticalTableDesigner"
set "SYSTEM_ROOT=%ProgramFiles%\FreeCAD 1.1"
set "SYSTEM_DEST=%SYSTEM_ROOT%\Mod\OpticalTableDesigner"

if not exist "%SOURCE%\InitGui.py" (
    echo ERROR: Workbench source files not found.
    echo Extract the whole ZIP first. Keep this BAT beside the OpticalTableDesigner folder.
    goto :fail
)

echo Close FreeCAD before updating an existing workbench.
echo.
echo Choose where to install the workbench:
echo.
echo   [1] Current user only (recommended; no admin access required)
echo       %USER_DEST%
echo.
echo   [2] All users (requires 'Run as administrator')
echo       %SYSTEM_DEST%
echo.
choice /C 12 /N /M "Select 1 or 2: "
if errorlevel 2 goto :all_users
if errorlevel 1 goto :current_user

goto :fail

:current_user
set "DEST=%USER_DEST%"
echo.
echo Installing for current user...
if not exist "%APPDATA%\FreeCAD\v1-1\Mod" mkdir "%APPDATA%\FreeCAD\v1-1\Mod"
if not exist "%APPDATA%\FreeCAD\v1-1\Mod" goto :permission_error
goto :copy_files

:all_users
set "DEST=%SYSTEM_DEST%"
echo.
echo Installing for all users...
if not exist "%SYSTEM_ROOT%" (
    echo ERROR: Expected FreeCAD location not found: "%SYSTEM_ROOT%"
    echo This installer targets your C:\Program Files\FreeCAD 1.1 installation.
    echo Select option 1, or adjust SYSTEM_ROOT in this script if needed.
    goto :fail
)
if not exist "%SYSTEM_ROOT%\Mod" (
    echo ERROR: FreeCAD Mod folder not found: "%SYSTEM_ROOT%\Mod"
    echo Confirm your FreeCAD installation directory before installing.
    goto :fail
)
goto :copy_files

:copy_files
if not exist "%DEST%" mkdir "%DEST%"
if not exist "%DEST%" goto :permission_error

echo.
echo Copying workbench to:
echo   %DEST%
echo.
xcopy "%SOURCE%\*" "%DEST%\" /E /I /Y
if errorlevel 1 (
    echo.
    echo ERROR: Some files could not be copied.
    echo For option 2, right-click INSTALL_WINDOWS.bat and choose Run as administrator.
    goto :fail
)
if not exist "%DEST%\InitGui.py" (
    echo ERROR: Installation verification failed: InitGui.py is missing.
    goto :fail
)

echo.
echo ============================================================
echo v0.3 installation complete: %DEST%
echo ============================================================
echo.
echo 1. Close and reopen FreeCAD 1.1.
echo 2. In the workbench selector choose "Optical Table Designer".
echo 3. Confirm the dock panel title includes v0.3.
echo 4. Open your existing FCStd file; no reimport is needed.
echo 5. Optionally install Optics Workbench via Tools ^> Addon Manager.
echo.
echo To confirm FreeCAD's CURRENT-USER workbench path, open the Python
echo console inside FreeCAD and enter:
echo   import FreeCAD as App; print(App.getUserAppDataDir())
echo.
pause
exit /b 0

:permission_error
echo.
echo ERROR: Cannot create destination directory:
echo   %DEST%
echo If you selected option 2, right-click BAT and choose Run as administrator.
:fail
echo.
echo Installation unsuccessful; nothing was registered with FreeCAD.
pause
exit /b 1
