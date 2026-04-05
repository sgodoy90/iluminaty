@echo off
setlocal EnableDelayedExpansion
title ILUMINATY

:: -- Detect venv ----------------------------------------
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else if exist ".venv312\Scripts\python.exe" (
    set PYTHON=.venv312\Scripts\python.exe
) else (
    echo ERROR: Virtual environment not found.
    echo Run install.bat first.
    pause
    exit /b 1
)

:: -- Read key from env or .env file ----------------------------------------
if not defined ILUMINATY_KEY (
    if exist ".env" (
        for /f "tokens=1,2 delims==" %%a in (.env) do (
            if "%%a"=="ILUMINATY_KEY" set ILUMINATY_KEY=%%b
        )
    )
)

if not defined ILUMINATY_KEY (
    set ILUMINATY_KEY=your-key-here
)

:: -- Parse args ----------------------------------------
set PORT=8420
set FPS=3
set MONITOR=0
set EXTRA_ARGS=

:parse
if "%1"=="" goto start
if "%1"=="--port" (set PORT=%2& shift & shift & goto parse)
if "%1"=="--fps"  (set FPS=%2&  shift & shift & goto parse)
if "%1"=="--monitor" (set MONITOR=%2& shift & shift & goto parse)
set EXTRA_ARGS=%EXTRA_ARGS% %1
shift
goto parse

:start
echo.
echo  ILUMINATY starting...
echo  Port: %PORT%  FPS: %FPS%  Monitor: %MONITOR%
echo  Key:  %ILUMINATY_KEY:~0,4%...[redacted]
echo.

%PYTHON% -u main.py start ^
    --port %PORT% ^
    --fps %FPS% ^
    --actions ^
    --api-key %ILUMINATY_KEY% ^
    %EXTRA_ARGS%
