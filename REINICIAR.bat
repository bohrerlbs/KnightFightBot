@echo off
cd /d "%~dp0"
echo [1/4] Encerrando bots e launcher...
taskkill /F /IM python.exe /T > nul 2>&1
taskkill /F /IM pythonw.exe /T > nul 2>&1
taskkill /F /IM ngrok.exe /T > nul 2>&1
timeout /t 2 /nobreak > nul

echo [2/4] Baixando atualizacao do GitHub...
python -c "
import urllib.request, zipfile, shutil, os
from pathlib import Path

base = Path(r'%~dp0')
raw  = 'https://raw.githubusercontent.com/bohrerlbs/KnightFightBot/main'
files = ['launcher.py','launcher.html','dashboard.html','dashboard_bg.html',
         'login.html','bot.py','bot_bg.py','combat_sim.py','VERSION','VERSION_BG']

updated = 0
for f in files:
    try:
        url = f'{raw}/{f}'
        data = urllib.request.urlopen(url, timeout=10).read()
        (base / f).write_bytes(data)
        print(f'  OK {f}')
        updated += 1
    except Exception as e:
        print(f'  SKIP {f}: {e}')
print(f'Atualizados: {updated}/{len(files)}')
"
if errorlevel 1 (
    echo AVISO: Erro ao atualizar. Reiniciando com versao atual...
)

echo [3/4] Iniciando launcher...
timeout /t 1 /nobreak > nul
start "" pythonw launcher.py

echo [4/4] Pronto! O launcher esta subindo em background.
echo        Acesse: http://localhost:8764/launcher
timeout /t 3 /nobreak > nul
