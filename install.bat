@echo off
setlocal EnableDelayedExpansion
title ILUMINATY Installer

echo.
echo  ============================================
echo   ILUMINATY - Installation
echo   Real-time visual perception for AI
echo  ============================================
echo.

:: -- Check Python ----------------------------------------
echo [1/5] Checking Python...

set PYTHON_CMD=
for %%p in (python python3 py) do (
    if not defined PYTHON_CMD (
        %%p --version >nul 2>&1
        if !errorlevel! equ 0 (
            for /f "tokens=2" %%v in ('%%p --version 2^>^&1') do (
                for /f "tokens=1,2 delims=." %%a in ("%%v") do (
                    if %%a geq 3 if %%b geq 10 (
                        set PYTHON_CMD=%%p
                    )
                )
            )
        )
    )
)

if not defined PYTHON_CMD (
    echo.
    echo  ERROR: Python 3.10+ not found.
    echo  Download from: https://python.org/downloads
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('!PYTHON_CMD! --version 2^>^&1') do set PY_VERSION=%%v
echo  OK  Python !PY_VERSION! found: !PYTHON_CMD!

:: -- Create virtual environment ----------------------------------------
echo [2/5] Creating virtual environment (.venv)...

if exist ".venv\Scripts\python.exe" (
    echo  OK  .venv already exists, skipping creation
) else (
    !PYTHON_CMD! -m venv .venv
    if !errorlevel! neq 0 (
        echo  ERROR: Failed to create virtual environment
        pause
        exit /b 1
    )
    echo  OK  .venv created
)

set VENV_PYTHON=.venv\Scripts\python.exe

:: -- Install dependencies ----------------------------------------
echo [3/5] Installing dependencies (this may take 1-2 minutes)...

!VENV_PYTHON! -m pip install --upgrade pip -q
if !errorlevel! neq 0 (
    echo  ERROR: pip upgrade failed
    pause
    exit /b 1
)

!VENV_PYTHON! -m pip install -e ".[ocr]" -q
if !errorlevel! neq 0 (
    echo  ERROR: Installation failed
    echo  Try running: !VENV_PYTHON! -m pip install -e .[ocr] --verbose
    pause
    exit /b 1
)

echo  OK  Core + OCR dependencies installed

:: -- Generate MCP config ----------------------------------------
echo [4/5] Generating MCP configuration...

set REPO_DIR=%CD%
set MCP_CONFIG_DIR=%APPDATA%\Claude
set MCP_CONFIG=%MCP_CONFIG_DIR%\claude_desktop_config.json

if not exist "%MCP_CONFIG_DIR%" mkdir "%MCP_CONFIG_DIR%" 2>nul

:: Check if config already exists and has iluminaty entry
set ALREADY_CONFIGURED=0
if exist "%MCP_CONFIG%" (
    findstr /i "iluminaty" "%MCP_CONFIG%" >nul 2>&1
    if !errorlevel! equ 0 set ALREADY_CONFIGURED=1
)

if !ALREADY_CONFIGURED! equ 1 (
    echo  OK  Claude MCP config already has iluminaty entry
    echo      Edit: %MCP_CONFIG%
) else (
    :: Write MCP config
    (
        echo {
        echo   "mcpServers": {
        echo     "iluminaty": {
        echo       "command": "!REPO_DIR!\.venv\Scripts\python.exe",
        echo       "args": ["!REPO_DIR!\run_mcp.py"],
        echo       "env": {
        echo         "ILUMINATY_API_URL": "http://127.0.0.1:8420",
        echo         "ILUMINATY_KEY": "your-key-here"
        echo       }
        echo     }
        echo   }
        echo }
    ) > "%MCP_CONFIG%"

    echo  OK  MCP config written: %MCP_CONFIG%
    echo      Edit ILUMINATY_KEY with your key (or leave as-is for free mode^)
)

:: Also write .mcp.json for Claude Code / other MCP clients
(
    echo {
    echo   "mcpServers": {
    echo     "iluminaty": {
    echo       "command": "!REPO_DIR!\.venv\Scripts\python.exe",
    echo       "args": ["!REPO_DIR!\run_mcp.py"],
    echo       "env": {
    echo         "ILUMINATY_API_URL": "http://127.0.0.1:8420",
    echo         "ILUMINATY_KEY": "your-key-here"
    echo       }
    echo     }
    echo   }
    echo }
) > ".mcp.json"
echo  OK  .mcp.json written for Claude Code

:: -- Verify install ----------------------------------------
echo [5/5] Verifying installation...

!VENV_PYTHON! -c "import iluminaty; import mss; import PIL; print('OK')" 2>nul
if !errorlevel! neq 0 (
    echo  WARNING: Verification import failed - may still work, check manually
) else (
    echo  OK  Import check passed
)

:: -- Done ----------------------------------------
echo.
echo  ============================================
echo   Installation complete!
echo  ============================================
echo.
echo  Next steps:
echo.
echo  1. Start ILUMINATY:
echo        start.bat
echo.
echo  2. Connect Claude Code:
echo        Add to your .mcp.json - already written to: .mcp.json
echo.
echo  3. In Claude, type:
echo        call see_now to see your screen
echo.
echo  Docs: https://github.com/sgodoy90/iluminaty
echo.
pause
