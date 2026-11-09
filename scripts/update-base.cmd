@echo off
setlocal
rem Drag a stock boot.img onto this file (or pass its path) to re-base the
rem kernel build on new HyperOS firmware. Extra options are forwarded to
rem scripts\update-base.py, for example:
rem   update-base.cmd new-boot.img --release v1.1.0

if "%~1"=="" goto usage

set "BOOT=%~1"

echo Checking %BOOT% ...
python "%~dp0update-base.py" --boot "%BOOT%" --dry-run
if errorlevel 1 goto done

choice /c YN /n /m "Proceed with the full rebuild (commit, CI build, repack)? [Y/N] "
if errorlevel 2 goto done

python "%~dp0update-base.py" --boot "%BOOT%" --push --wait --repack %2 %3 %4 %5 %6 %7 %8 %9
goto done

:usage
echo Usage: drag a stock boot.img onto this file, or run:
echo   %~nx0 path\to\boot.img [--release v1.1.0]
echo.
echo The script extracts the kernel and config, updates the pinned GKI commit
echo and release string, triggers the GitHub Actions build, downloads the
echo artifacts and repacks a flashable boot image.

:done
echo.
pause
