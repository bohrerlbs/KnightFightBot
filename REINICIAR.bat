@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "TMPFILE=%TEMP%\kfbot_profiles.tmp"
if exist "%TMPFILE%" del "%TMPFILE%"

echo Detectando bots em execucao...
for /f "skip=1 delims=" %%L in ('wmic process where "name='python.exe'" get commandline 2^>nul') do (
    set "LINE=%%L"
    echo !LINE! | findstr /i /c:"bot.py" | findstr /i /c:"--profile" > nul 2>&1
    if not errorlevel 1 (
        set "REST=!LINE:*--profile =!"
        for /f "tokens=1 delims= " %%P in ("!REST!") do (
            echo %%P >> "%TMPFILE%"
        )
    )
)

echo Encerrando bots e launcher...
taskkill /F /IM python.exe /T > nul 2>&1
taskkill /F /IM pythonw.exe /T > nul 2>&1
taskkill /F /IM ngrok.exe /T > nul 2>&1
timeout /t 2 /nobreak > nul

echo Iniciando launcher...
start "" cmd /c "INICIAR.bat"
timeout /t 3 /nobreak > nul

echo Reiniciando bots...
if exist "%TMPFILE%" (
    for /f "usebackq delims=" %%P in ("%TMPFILE%") do (
        set "PROF=%%P"
        set "PROF=!PROF: =!"
        if exist "iniciar_!PROF!.bat" (
            echo   Iniciando: !PROF!
            start "" cmd /c "iniciar_!PROF!.bat"
            timeout /t 1 /nobreak > nul
        ) else (
            echo   [AVISO] iniciar_!PROF!.bat nao encontrado — pulando
        )
    )
    del "%TMPFILE%"
) else (
    echo Nenhum bot estava rodando.
)

echo.
echo Reinicio completo!
timeout /t 2 /nobreak > nul
