@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "TMPFILE=%TEMP%\kfbot_profiles.tmp"
set "TMPFILE_BG=%TEMP%\kfbot_profiles_bg.tmp"
set "ALLCMDS=%TEMP%\kfbot_allcmds.tmp"
set "RAWMAIN=%TEMP%\kfbot_rawmain.tmp"
set "RAWBG=%TEMP%\kfbot_rawbg.tmp"
for %%F in ("%TMPFILE%" "%TMPFILE_BG%" "%ALLCMDS%" "%RAWMAIN%" "%RAWBG%") do (
    if exist %%F del /q %%F
)

echo Detectando bots em execucao...
REM Grava todas as command lines num arquivo (nunca via echo de variavel - evita
REM que caracteres especiais |/&/> dentro da command line de outro processo sejam
REM interpretados como operadores do cmd e acabem executando comandos por acidente).
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Select-Object -ExpandProperty CommandLine" > "%ALLCMDS%"

findstr /i /c:"bot.py" "%ALLCMDS%" | findstr /i /c:"--workdir" > "%RAWMAIN%" 2>nul
findstr /i /c:"bot_bg.py" "%ALLCMDS%" | findstr /i /c:"--workdir" > "%RAWBG%" 2>nul

if exist "%RAWMAIN%" (
    for /f "usebackq delims=" %%L in ("%RAWMAIN%") do (
        set "LINE=%%L"
        set "REST=!LINE:*profiles\=!"
        for /f "tokens=1 delims= " %%P in ("!REST!") do (
            echo %%P >> "%TMPFILE%"
        )
    )
)
if exist "%RAWBG%" (
    for /f "usebackq delims=" %%L in ("%RAWBG%") do (
        set "LINE=%%L"
        set "REST=!LINE:*profiles\=!"
        for /f "tokens=1 delims= " %%P in ("!REST!") do (
            echo %%P >> "%TMPFILE_BG%"
        )
    )
)
del /q "%ALLCMDS%" "%RAWMAIN%" "%RAWBG%" 2>nul

echo Encerrando bots e launcher...
taskkill /F /IM python.exe /T > nul 2>&1
taskkill /F /IM pythonw.exe /T > nul 2>&1
taskkill /F /IM cloudflared.exe /T > nul 2>&1
timeout /t 2 /nobreak > nul

echo Iniciando launcher...
start "" cmd /c "INICIAR.bat"

echo Aguardando launcher responder na porta 8764...
set "LAUNCHER_OK=0"
for /l %%i in (1,1,30) do (
    if "!LAUNCHER_OK!"=="0" (
        powershell -NoProfile -Command "(Test-NetConnection -ComputerName 127.0.0.1 -Port 8764 -InformationLevel Quiet)" | findstr /i "True" > nul
        if not errorlevel 1 (
            set "LAUNCHER_OK=1"
        ) else (
            timeout /t 1 /nobreak > nul
        )
    )
)
if "!LAUNCHER_OK!"=="0" (
    echo   [AVISO] Launcher nao respondeu em 30s - tentando iniciar bots assim mesmo...
)

echo Iniciando Cloudflare Tunnel...
start "" /min cmd /c "cloudflared.exe tunnel run kfbot"

echo Reiniciando bots (em segundo plano, via launcher)...
if exist "%TMPFILE%" (
    for /f "usebackq delims=" %%P in ("%TMPFILE%") do (
        set "PROF=%%P"
        set "PROF=!PROF: =!"
        echo   Iniciando: !PROF!
        powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8764/api/start' -Method Post -Body (@{name='!PROF!'} | ConvertTo-Json) -ContentType 'application/json' -TimeoutSec 10; if (-not $r.ok) { Write-Host ('    [AVISO] ' + $r.error) } } catch { Write-Host ('    [ERRO] ' + $_.Exception.Message) }"
        timeout /t 1 /nobreak > nul
    )
    del /q "%TMPFILE%"
) else (
    echo Nenhum bot estava rodando.
)

echo Reiniciando bots de BattleGround (em segundo plano, via launcher)...
if exist "%TMPFILE_BG%" (
    for /f "usebackq delims=" %%P in ("%TMPFILE_BG%") do (
        set "PROF=%%P"
        set "PROF=!PROF: =!"
        if exist "profiles\!PROF!\config_bg.json" (
            echo   Iniciando BG: !PROF!
            powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8764/api/bg/start/!PROF!' -Method Post -Body '{}' -ContentType 'application/json' -TimeoutSec 10; if (-not $r.ok) { Write-Host ('    [AVISO] ' + $r.error) } } catch { Write-Host ('    [ERRO] ' + $_.Exception.Message) }"
            timeout /t 1 /nobreak > nul
        ) else (
            echo   [AVISO] config_bg.json de !PROF! nao encontrado - pulando BG
        )
    )
    del /q "%TMPFILE_BG%"
) else (
    echo Nenhum bot de BG estava rodando.
)

echo.
echo Reinicio completo!
timeout /t 2 /nobreak > nul
