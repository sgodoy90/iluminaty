@echo off
set ILUMINATY_KEY=ILUM-dev-local
set ILUMINATY_NO_AUTH=1
cd /d C:\Users\jgodo\Desktop\iluminaty
.venv312\Scripts\python.exe -u main.py start --port 8420 --fps 3 --actions
