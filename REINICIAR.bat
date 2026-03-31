@echo off
cd /d "%~dp0"
echo Encerrando bots e launcher...
taskkill /F /IM python.exe /T > nul 2>&1
taskkill /F /IM pythonw.exe /T > nul 2>&1
taskkill /F /IM ngrok.exe /T > nul 2>&1
timeout /t 2 /nobreak > nul

echo Iniciando launcher...
call INICIAR.bat
