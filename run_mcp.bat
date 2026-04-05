@echo off
:: ILUMINATY_KEY should be set in your environment or .env file
:: Do NOT hardcode keys here — this file may be committed to version control
if exist "%~dp0.env" (
    for /f "tokens=1,2 delims==" %%a in (%~dp0.env) do (
        if "%%a"=="ILUMINATY_KEY" set ILUMINATY_KEY=%%b
    )
)
"%~dp0.venv312\Scripts\python.exe" "%~dp0run_mcp.py"
