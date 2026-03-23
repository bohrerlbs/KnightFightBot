@echo off
:: Aguarda o launcher.py terminar de escrever o novo arquivo
timeout /t 2 /nobreak > nul

:: Substitui launcher.py pelo novo
if exist "%~dp0launcher.py.new" (
    copy /y "%~dp0launcher.py.new" "%~dp0launcher.py" > nul
    del "%~dp0launcher.py.new" > nul
)

:: Reinicia o launcher
cd /d "%~dp0"
start "" pythonw launcher.py
