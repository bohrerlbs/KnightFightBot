@echo off
chcp 65001 >nul
echo.
echo  ██╗  ██╗███╗   ██╗██╗ ██████╗ ██╗  ██╗████████╗███████╗██╗ ██████╗ ██╗  ██╗████████╗
echo  ██║ ██╔╝████╗  ██║██║██╔════╝ ██║  ██║╚══██╔══╝██╔════╝██║██╔════╝ ██║  ██║╚══██╔══╝
echo  █████╔╝ ██╔██╗ ██║██║██║  ███╗███████║   ██║   █████╗  ██║██║  ███╗███████║   ██║   
echo  ██╔═██╗ ██║╚██╗██║██║██║   ██║██╔══██║   ██║   ██╔══╝  ██║██║   ██║██╔══██║   ██║   
echo  ██║  ██╗██║ ╚████║██║╚██████╔╝██║  ██║   ██║   ██║     ██║╚██████╔╝██║  ██║   ██║   
echo  ╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   
echo                                    B O T
echo.
echo  Instalador v1.0.2
echo  ══════════════════════════════════════════
echo.

:: Verifica Python
echo  [1/4] Verificando Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  ╔══════════════════════════════════════════════════════════╗
    echo  ║  PYTHON NAO ENCONTRADO!                                  ║
    echo  ║                                                          ║
    echo  ║  Para usar o KnightFight Bot voce precisa instalar       ║
    echo  ║  o Python primeiro.                                      ║
    echo  ║                                                          ║
    echo  ║  1. Acesse: https://www.python.org/downloads/            ║
    echo  ║  2. Baixe a versao mais recente                          ║
    echo  ║  3. Na instalacao, marque "Add Python to PATH"           ║
    echo  ║  4. Apos instalar, execute este arquivo novamente        ║
    echo  ║                                                          ║
    echo  ╚══════════════════════════════════════════════════════════╝
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  OK - %PYVER% encontrado
echo.

:: Verifica pip e instala dependencias
echo  [2/4] Instalando dependencias...
pip install requests beautifulsoup4 lxml selenium webdriver-manager --quiet --break-system-packages 2>nul
pip install requests beautifulsoup4 lxml selenium webdriver-manager --quiet 2>nul
if %errorlevel% neq 0 (
    echo.
    echo  AVISO: Erro ao instalar dependencias automaticamente.
    echo  Tente manualmente: pip install requests beautifulsoup4 lxml selenium webdriver-manager
    echo.
)
echo  OK - Dependencias instaladas
echo.

:: Copia arquivos para pasta de instalacao
echo  [3/4] Instalando...
set DEST=%USERPROFILE%\KnightFightBot
mkdir "%DEST%" 2>nul
xcopy /E /Y /I "%~dp0*" "%DEST%\" >nul
echo  OK - Instalado em %DEST%
echo.

:: Cria atalho na area de trabalho
echo  [4/4] Criando atalho...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%USERPROFILE%\Desktop\KnightFight Bot.lnk'); $sc.TargetPath = '%DEST%\KnightFightBot.exe'; $sc.WorkingDirectory = '%DEST%'; $sc.IconLocation = '%DEST%\KnightFightBot.exe'; $sc.Save()" >nul 2>&1
echo  OK - Atalho criado na area de trabalho
echo.

echo  ══════════════════════════════════════════
echo  Instalacao concluida com sucesso!
echo.
echo  Um atalho foi criado na sua area de trabalho.
echo  Clique duas vezes em "KnightFight Bot" para iniciar.
echo  ══════════════════════════════════════════
echo.

choice /C SN /M "Deseja iniciar o bot agora?"
if %errorlevel% equ 1 (
    start "" "%DEST%\KnightFightBot.exe"
)

exit /b 0
