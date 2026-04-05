@echo off
set ILUMINATY_KEY=ILUM-dev-local
rem NOTE: ILUMINATY_NO_AUTH removed — use --api-key instead (auth always enforced)
cd /d C:\Users\jgodo\Desktop\iluminaty
.venv312\Scripts\python.exe -u main.py start --port 8420 --fps 3 --actions --api-key %ILUMINATY_KEY%
