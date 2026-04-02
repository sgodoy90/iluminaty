@echo off
title ILUMINATY — Full Stack
color 0A

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║         ILUMINATY — Full Stack Launcher           ║
echo  ║   IPA Vision + Brain LLM + Web Chat UI           ║
echo  ╚══════════════════════════════════════════════════╝
echo.

cd /d "C:\Users\jgodo\Desktop\iluminaty"

REM ── 1. Start ILUMINATY perception server (port 8420) ──────────────────────
echo  [1/2] Iniciando ILUMINATY IPA (percepcion + acciones)...

set ILUMINATY_VLM_CAPTION=1
set ILUMINATY_VLM_BACKEND=smol
set ILUMINATY_VLM_MODEL=HuggingFaceTB/SmolVLM2-500M-Instruct
set ILUMINATY_VLM_INT8=1
set ILUMINATY_VLM_IMAGE_SIZE=384
set ILUMINATY_VLM_MAX_TOKENS=64
set ILUMINATY_VLM_MIN_INTERVAL_MS=900
set ILUMINATY_VLM_KEEPALIVE_MS=7000
set ILUMINATY_VLM_PRIORITY_THRESHOLD=0.55
set ILUMINATY_VLM_SECONDARY_HEARTBEAT_S=8

start "ILUMINATY IPA" cmd /k "py -3.13 -m iluminaty.main --monitor 0 --fps 2 --fast-loop-hz 8 --deep-loop-hz 0.6"

echo  [1/2] ILUMINATY IPA iniciando en background (puerto 8420)...
echo.

REM ── 2. Wait for IPA to be ready ────────────────────────────────────────────
echo  Esperando que IPA este listo...
:wait_loop
timeout /t 3 /nobreak >nul
py -3.13 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8420/perception/world', timeout=2); exit(0)" 2>nul
if %errorlevel% == 0 goto ipa_ready
echo  . esperando IPA...
goto wait_loop

:ipa_ready
echo  [OK] IPA listo en http://127.0.0.1:8420
echo.

REM ── 3. Start Brain server (port 8421) ─────────────────────────────────────
echo  [2/2] Iniciando IluminatyBrain (LLM local + Web UI)...
echo  Abriendo http://localhost:8421 en el browser...
echo.

py -3.13 -m iluminaty.brain_server --4bit

pause
