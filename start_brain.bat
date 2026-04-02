@echo off
title IluminatyBrain Web UI
echo.
echo  =============================================
echo   ILUMINATY Brain - Web Chat UI
echo   Abre en http://localhost:8421
echo  =============================================
echo.

cd /d "C:\Users\jgodo\Desktop\iluminaty"

REM Modelo default: Qwen2.5-3B en INT4 (requiere ~1.5GB VRAM)
REM Cambia --model para usar otro modelo de HuggingFace
REM Agrega --gguf C:\ruta\modelo.gguf para usar un GGUF local

py -3.13 -m iluminaty.brain_server --4bit

pause
