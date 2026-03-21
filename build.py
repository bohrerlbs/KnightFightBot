"""
Build script — empacota o KnightFight Bot em .exe usando PyInstaller.
Uso: python build.py
"""
import subprocess, sys, os, shutil
from pathlib import Path

ROOT = Path(__file__).parent

def run(cmd):
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print(f"  ✗ Falhou (código {r.returncode})")
        sys.exit(1)

def check_deps():
    print("\n[1/4] Verificando dependências...")
    deps = ["pyinstaller", "selenium", "webdriver_manager", "requests", "beautifulsoup4", "lxml"]
    for dep in deps:
        try:
            __import__(dep.replace("-","_"))
            print(f"  ✓ {dep}")
        except ImportError:
            print(f"  ↓ Instalando {dep}...")
            run([sys.executable, "-m", "pip", "install", dep, "--break-system-packages"])

def build_exe():
    print("\n[2/4] Empacotando com PyInstaller...")

    # Lista todos os arquivos HTML para incluir
    data_files = []
    for f in ["launcher.html", "dashboard.html"]:
        if (ROOT / f).exists():
            data_files += ["--add-data", f"{f};."]

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",                      # tudo em um único .exe
        "--windowed",                     # sem janela de console
        "--name", "KnightFightBot",
        "--icon", "icon.ico" if (ROOT/"icon.ico").exists() else "NONE",
        "--hidden-import", "selenium",
        "--hidden-import", "webdriver_manager",
        "--hidden-import", "webdriver_manager.chrome",
        "--hidden-import", "webdriver_manager.microsoft",
        "--hidden-import", "webdriver_manager.firefox",
        "--hidden-import", "bs4",
        "--hidden-import", "lxml",
        "--collect-all", "selenium",
        "--collect-all", "webdriver_manager",
        *data_files,
        "launcher.py",
    ]
    run(cmd)

def create_installer_folder():
    print("\n[3/4] Criando pasta de distribuição...")
    dist = ROOT / "dist" / "KnightFightBot"
    dist.mkdir(parents=True, exist_ok=True)

    # Copia o .exe
    exe_src = ROOT / "dist" / "KnightFightBot.exe"
    if exe_src.exists():
        shutil.copy(exe_src, dist / "KnightFightBot.exe")

    # Copia arquivos necessários
    for f in ["bot.py", "dashboard.html", "launcher.html", "requirements.txt"]:
        src = ROOT / f
        if src.exists():
            shutil.copy(src, dist / f)

    # Cria README
    readme = """# KnightFight Bot

## Como usar:
1. Dê duplo clique em KnightFightBot.exe
2. O browser abrirá automaticamente em http://localhost:8764
3. Clique em "+ Novo Perfil"
4. Clique em "Abrir browser e capturar cookie"
5. Faça login no jogo e aguarde
6. Configure nome e clique em "Criar Perfil e Iniciar Bot"

## Para rodar múltiplos personagens:
- Crie um perfil para cada personagem
- Cada um roda em uma porta diferente (8765, 8766, ...)
- Use o Launcher para gerenciar todos

## Requisitos:
- Windows 10/11
- Chrome, Edge ou Firefox instalado
"""
    (dist / "README.txt").write_text(readme, encoding="utf-8")

    print(f"  ✓ Pasta: {dist}")
    return dist

def create_atalho():
    """Cria um instalador simples (.bat) que adiciona atalho na área de trabalho."""
    print("\n[4/4] Criando instalador...")
    installer = """@echo off
echo Instalando KnightFight Bot...
set DEST=%USERPROFILE%\\KnightFightBot
mkdir "%DEST%" 2>nul
xcopy /E /Y /I "%~dp0*" "%DEST%\\" >nul

:: Atalho na área de trabalho
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%USERPROFILE%\\Desktop\\KnightFight Bot.lnk'); $sc.TargetPath = '%DEST%\\KnightFightBot.exe'; $sc.WorkingDirectory = '%DEST%'; $sc.IconLocation = '%DEST%\\KnightFightBot.exe'; $sc.Save()"

echo.
echo Instalacao concluida!
echo Atalho criado na area de trabalho.
echo.
start "" "%DEST%\\KnightFightBot.exe"
pause
"""
    installer_path = ROOT / "dist" / "KnightFightBot" / "Instalar.bat"
    installer_path.write_text(installer, encoding="utf-8")
    print(f"  ✓ Instalar.bat criado")

if __name__ == "__main__":
    print("⚔  KnightFight Bot — Build Script")
    print("="*40)
    check_deps()
    build_exe()
    dist = create_installer_folder()
    create_atalho()
    print(f"""
✅ Build concluído!

Para distribuir: compacte a pasta dist/KnightFightBot/ em um .zip
Seus amigos só precisam:
  1. Extrair o .zip
  2. Rodar Instalar.bat
  3. Usar o atalho na área de trabalho

Pasta: {dist}
""")
