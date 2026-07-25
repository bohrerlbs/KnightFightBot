@echo off
chcp 65001 >nul
echo.
echo  KnightFight Bot — Instalador de dependencias
echo  =============================================
echo.

:check_python
echo [1/2] Verificando Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  PYTHON NAO ENCONTRADO!
    echo.
    echo  Vou abrir o site para download agora.
    echo  Instale o Python e marque a opcao:
    echo.
    echo     [x] Add Python to PATH
    echo.
    echo  Apos instalar, feche e abra um novo CMD
    echo  e rode este arquivo novamente.
    echo.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  OK - %PYVER%
echo.

echo [2/2] Instalando dependencias...
pip install requests beautifulsoup4 lxml selenium webdriver-manager
if %errorlevel% neq 0 (
    echo.
    echo  Tentando novamente com --break-system-packages...
    pip install requests beautifulsoup4 lxml selenium webdriver-manager --break-system-packages
)
echo.

:: Verifica de verdade se os modulos ficaram importaveis (pip pode "ter sucesso"
:: e mesmo assim faltar um pacote, ex: selenium, se o pip resolver conflito e pular ele)
python -c "import requests, bs4, lxml, selenium, webdriver_manager" >nul 2>&1
if %errorlevel% neq 0 (
    echo  =============================================
    echo  ERRO: nem todas as dependencias ficaram instaladas!
    echo  =============================================
    echo.
    echo  Rode manualmente no CMD e veja a mensagem de erro completa:
    echo    pip install requests beautifulsoup4 lxml selenium webdriver-manager
    echo.
    pause
    exit /b 1
)

echo  OK - Dependencias instaladas!
echo.
echo  =============================================
echo  Tudo pronto! Execute: iniciar_launcher.bat
echo  =============================================
echo.
pause
