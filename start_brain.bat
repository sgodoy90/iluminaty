@echo off
title IluminatyBrain — Full Stack
color 0A

echo.
echo  Iniciando IluminatyBrain (IPA + LLM + Web UI)...
echo  Abre en http://localhost:8421
echo.

cd /d "C:\Users\jgodo\Desktop\iluminaty"

py -3.13 -m iluminaty.brain_server --4bit

pause
