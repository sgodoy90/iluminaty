@echo off
title ILUMINATY
color 0A

echo.
echo  ==========================================
echo   ILUMINATY - Perception + MCP Server
echo   Dashboard: http://localhost:8420
echo  ==========================================
echo.

cd /d "C:\Users\jgodo\Desktop\iluminaty"

REM ── Read GPU config from iluminaty_config.json ─────────────────────────────
set ILUMINATY_VLM_DEVICE=auto
for /f "delims=" %%A in ('py -3.13 -c "import json; c=json.load(open(\"iluminaty_config.json\")); print(c.get(\"vlm_device\",\"auto\"))" 2^>nul') do set ILUMINATY_VLM_DEVICE=%%A

echo  VLM Device: %ILUMINATY_VLM_DEVICE%
echo.

REM ── VLM settings ───────────────────────────────────────────────────────────
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

py -3.13 -m iluminaty.main --monitor 0 --fps 2 --fast-loop-hz 8 --deep-loop-hz 0.6
pause
