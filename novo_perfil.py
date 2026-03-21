"""
Cria um novo perfil para o KnightFight Bot.
Uso: python novo_perfil.py
"""
import os, json

print("\n⚔  KnightFight Bot — Novo Perfil\n" + "="*40)

nome = input("Nome do perfil (ex: bohrer_int7, alt_int7): ").strip()
if not nome:
    print("Nome inválido.")
    exit(1)

servidores = ["int1","int2","int3","int4","int5","int6","int7"]
print(f"\nServidores disponíveis: {', '.join(servidores)}")
server = input("Servidor: ").strip().lower()
if server not in servidores:
    print("Servidor inválido.")
    exit(1)

userid = input("Seu UserID (ex: 522001100): ").strip()
print("\nComo pegar o cookie:")
print("  1. Abra o jogo no browser")
print("  2. F12 → Network → clique em qualquer requisição")
print("  3. Headers → Request Headers → Cookie")
print("  4. Copie o valor completo")
cookies = input("\nCookie: ").strip()

# Calcula porta baseado em perfis existentes
perfis_dir = "profiles"
porta = 8765
if os.path.exists(perfis_dir):
    existentes = len([d for d in os.listdir(perfis_dir) if os.path.isdir(os.path.join(perfis_dir, d))])
    porta = 8765 + existentes

porta_input = input(f"Porta do dashboard [{porta}]: ").strip()
if porta_input.isdigit():
    porta = int(porta_input)

# Cria pasta e config
pasta = os.path.join("profiles", nome)
os.makedirs(pasta, exist_ok=True)

config = {
    "profile": nome,
    "server": server,
    "userid": userid,
    "cookies": cookies,
    "port": porta,
}

with open(os.path.join(pasta, "config.json"), "w", encoding="utf-8") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)

# Cria script de inicialização
bat_content = f'@echo off\npython bot.py --profile {nome}\npause\n'
sh_content  = f'#!/bin/bash\npython3 bot.py --profile {nome}\n'

with open(f"iniciar_{nome}.bat", "w") as f:
    f.write(bat_content)
with open(f"iniciar_{nome}.sh", "w") as f:
    f.write(sh_content)

try:
    os.chmod(f"iniciar_{nome}.sh", 0o755)
except:
    pass

print(f"""
✅ Perfil '{nome}' criado!

Arquivos criados:
  profiles/{nome}/config.json  — configuração
  iniciar_{nome}.bat            — Windows
  iniciar_{nome}.sh             — Linux/Mac

Para iniciar:
  Windows: iniciar_{nome}.bat
  Linux:   ./iniciar_{nome}.sh
  Manual:  python bot.py --profile {nome}

Dashboard: http://localhost:{porta}/dashboard
""")
