@echo off
echo Instalando KnightFight Bot...
set DEST=%USERPROFILE%\KnightFightBot
mkdir "%DEST%" 2>nul
xcopy /E /Y /I "%~dp0*" "%DEST%\" >nul

:: Atalho na área de trabalho
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%USERPROFILE%\Desktop\KnightFight Bot.lnk'); $sc.TargetPath = '%DEST%\KnightFightBot.exe'; $sc.WorkingDirectory = '%DEST%'; $sc.IconLocation = '%DEST%\KnightFightBot.exe'; $sc.Save()"

echo.
echo Instalacao concluida!
echo Atalho criado na area de trabalho.
echo.
start "" "%DEST%\KnightFightBot.exe"
pause
