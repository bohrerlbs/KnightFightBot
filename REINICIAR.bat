@echo off
cd /d "%~dp0"
echo [1/3] Encerrando bots e launcher...
taskkill /F /IM python.exe /T > nul 2>&1
taskkill /F /IM pythonw.exe /T > nul 2>&1
taskkill /F /IM ngrok.exe /T > nul 2>&1
timeout /t 2 /nobreak > nul

echo [2/3] Baixando atualizacao do GitHub...
python -c "
import urllib.request
from pathlib import Path

base = Path(r'%~dp0')
raw  = 'https://raw.githubusercontent.com/bohrerlbs/KnightFightBot/main'
files = ['launcher.py','launcher.html','dashboard.html','dashboard_bg.html',
         'login.html','bot.py','bot_bg.py','combat_sim.py','VERSION','VERSION_BG']

updated = 0
for f in files:
    try:
        data = urllib.request.urlopen(f'{raw}/{f}', timeout=10).read()
        (base / f).write_bytes(data)
        print(f'  OK {f}')
        updated += 1
    except Exception as e:
        print(f'  SKIP {f}: {e}')
print(f'Atualizados: {updated}/{len(files)}')
"

echo [3/3] Iniciando launcher...
timeout /t 1 /nobreak > nul
python launcher.py
pause
