@echo off
set ILUMINATY_KEY=%ILUMINATY_KEY%
where python3.13 >nul 2>&1 && (python3.13 "%~dp0run_mcp.py" & exit /b)
where python3 >nul 2>&1 && (python3 "%~dp0run_mcp.py" & exit /b)
where python >nul 2>&1 && (python "%~dp0run_mcp.py" & exit /b)
echo ILUMINATY: Python not found. Install Python 3.12+ from python.org 1>&2
exit /b 1
