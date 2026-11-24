@echo off
setlocal
REM PaperGuard Lite - Double-Click Windows Launcher (auto-detects Python 3.11+)

set "SCRIPT_DIR=%~dp0"
set "PY_CMD="

for %%C in ("py -3.13" "py -3.12" "py -3.11" "py -3" "python" "python3") do (
  %%~C -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "PY_CMD=%%~C"
    goto :run
  )
)

echo ============================================================
echo  PaperGuard Lite requires Python 3.11 or newer.
echo ------------------------------------------------------------
echo  Install quickly via either option:
echo    1. Microsoft Store / winget: winget install Python.Python.3.12
echo    2. Official installer:       https://www.python.org/downloads/windows/
echo ============================================================
pause
exit /b 1

:run
echo [PaperGuard Lite] Starting local app...
%PY_CMD% "%SCRIPT_DIR%paperguard_app.py"
if errorlevel 1 pause
