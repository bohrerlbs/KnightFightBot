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
pip install requests beautifulsoup4 lxml selenium webdriver-manager --quiet
if %errorlevel% neq 0 (
    pip install requests beautifulsoup4 lxml selenium webdriver-manager --quiet --break-system-packages
)
echo  OK - Dependencias instaladas!
echo.
echo  =============================================
echo  Tudo pronto! Execute: iniciar_launcher.bat
echo  =============================================
echo.
pause
