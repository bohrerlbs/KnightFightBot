"""
analisar_dano.py
Analisa fórmulas de dano e defesa usando dados reais dos combates.

Compila de todos os perfis:
  - forca, agilidade, sk_1mao, sk_2maos, sk_armadura (eu e adv)
  - dano_causado real, dano_recebido real
  - dano simulado, def simulado

Uso:
    python analisar_dano.py C:\\Users\\Leonardo\\Downloads\\kfbot
"""

import json, pathlib, sys, math
from collections import defaultdict

BASE  = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")
SAIDA     = BASE / "analise_dano.json"
SAIDA_TXT = BASE / "analise_dano.txt"
ARQUIVOS  = ["combates_srv.json", "bg_combates.json"]

# ── Coleta ────────────────────────────────────────────────────────────────────
todos, fontes = [], []
for arq in ARQUIVOS:
    for f in sorted(BASE.rglob(arq)):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                perfil = f.parent.name
                for c in data:
                    c["_perfil"] = perfil
                    c["_fonte"]  = arq
                todos.extend(data)
                fontes.append(f"  {perfil}/{arq} ({len(data)})")
        except Exception as e:
            print(f"  ERRO {f}: {e}")

print(f"Total: {len(todos)} combates de {len(fontes)} arquivos")

# ── Filtra registros com dados úteis ─────────────────────────────────────────
# Precisa ter: forca, skill, dano real, rounds reais
validos = []
sem_forca = sem_dano = sem_rounds = 0
for c in todos:
    # Dados EU
    eu_frc = c.get("eu_frc", 0)
    eu_agi = c.get("eu_agi", 0)
    eu_s1  = c.get("eu_s1",  0)
    eu_s2  = c.get("eu_s2",  0)
    eu_arm = c.get("eu_arm", 0)
    dano_causado  = c.get("dano_causado",  0)
    dano_recebido = c.get("dano_recebido", 0)
    hits_eu  = c.get("hits_eu",  0)
    hits_adv = c.get("hits_adv", 0)
    rounds_eu  = c.get("rounds_real", 0) or (hits_eu + c.get("misses_eu", 0))

    if eu_frc == 0:
        sem_forca += 1; continue
    if dano_causado == 0 and dano_recebido == 0:
        sem_dano += 1; continue
    if hits_eu == 0 and hits_adv == 0:
        sem_rounds += 1; continue

    # Dano por hit real
    dano_por_hit_eu  = round(dano_causado  / hits_eu,  2) if hits_eu  > 0 else None
    dano_por_hit_adv = round(dano_recebido / hits_adv, 2) if hits_adv > 0 else None

    # Simulado
    sim_dano_eu  = c.get("sim_dano_eu",  0)
    sim_dano_adv = c.get("sim_dano_adv", 0)
    sim_def_eu   = c.get("sim_def_eu",   0)
    sim_def_adv  = c.get("sim_def_adv",  0)

    validos.append({
        "perfil": c.get("_perfil","?"),
        "resultado": c.get("resultado","?"),
        # EU stats
        "eu_frc": eu_frc, "eu_agi": eu_agi,
        "eu_s1": eu_s1, "eu_s2": eu_s2, "eu_arm": eu_arm,
        # ADV stats
        "adv_frc": c.get("adv_frc",0), "adv_agi": c.get("adv_agi",0),
        "adv_s1":  c.get("adv_s1",0),  "adv_s2":  c.get("adv_s2",0),
        "adv_arm": c.get("adv_arm",0),
        # Combate real
        "dano_causado": dano_causado, "dano_recebido": dano_recebido,
        "hits_eu": hits_eu, "hits_adv": hits_adv,
        "dano_por_hit_eu":  dano_por_hit_eu,
        "dano_por_hit_adv": dano_por_hit_adv,
        "dano_bloqueado_eu": c.get("dano_bloqueado_eu", 0),
        # Simulado
        "sim_dano_eu": sim_dano_eu, "sim_dano_adv": sim_dano_adv,
        "sim_def_eu":  sim_def_eu,  "sim_def_adv":  sim_def_adv,
        "erro_dano_eu":  round(dano_causado  - sim_dano_eu,  1) if sim_dano_eu  else None,
        "erro_dano_adv": round(dano_recebido - sim_dano_adv, 1) if sim_dano_adv else None,
    })

print(f"Válidos: {len(validos)} | Sem força: {sem_forca} | Sem dano: {sem_dano} | Sem rounds: {sem_rounds}")

# ── Fórmula bônus força ───────────────────────────────────────────────────────
# Hipótese: bonus_forca = (forca/10) * (dano_min+dano_max)/2 / 5.5
# Onde dano da arma depende do skill

def bonus_forca_formula(forca, sk_1mao, sk_2maos, divisor=5.5):
    """Retorna bonus de força estimado dado o skill ativo."""
    # Arma estimada pelo skill ativo
    sk = max(sk_1mao, sk_2maos)
    # Dano base da arma ~ (sk/10)² * fator
    # Por ora usamos a tabela do simulador
    return (forca / 10) * sk / divisor

linhas = []
L = linhas.append

L(f"\nANÁLISE DE DANO/DEFESA — KnightFight Bot")
L("="*65)
L(f"Arquivos: {len(fontes)}")
for f in fontes: L(f)
L(f"Total: {len(todos)} | Válidos: {len(validos)}")
L("")

# ── Tabela EU — dano por hit real vs stats ────────────────────────────────────
# Agrupa por forca para ver padrão
L(f"\n{'='*65}")
L("EU ATACANDO — dano por hit real vs força/skill")
L(f"{'Perfil':>10} {'Frc':>4} {'Agi':>4} {'Sk1':>4} {'Sk2':>4} {'Arm':>4} {'Hits':>5} {'DanoTotal':>10} {'D/hit':>7} {'SimDano':>8} {'Erro':>7}")
L("-"*80)

por_forca = defaultdict(list)
for r in validos:
    if r["hits_eu"] >= 3 and r["dano_causado"] > 0:
        por_forca[r["eu_frc"]].append(r)

for frc in sorted(por_forca.keys()):
    rs = por_forca[frc]
    total_hits  = sum(r["hits_eu"] for r in rs)
    total_dano  = sum(r["dano_causado"] for r in rs)
    d_hit = total_dano / total_hits if total_hits > 0 else 0
    sim_d = sum(r["sim_dano_eu"] for r in rs if r["sim_dano_eu"]) / len(rs) if rs else 0
    sk1 = round(sum(r["eu_s1"] for r in rs)/len(rs))
    sk2 = round(sum(r["eu_s2"] for r in rs)/len(rs))
    arm = round(sum(r["eu_arm"] for r in rs)/len(rs))
    agi = round(sum(r["eu_agi"] for r in rs)/len(rs))
    n = len(rs)
    sim_str = f"{sim_d:>7.1f}" if sim_d > 0 else "       ?"
    err_str = f"{d_hit-sim_d:>+6.1f}" if sim_d > 0 else "      ?"
    L(f"{'('+str(n)+'x)':>10} {frc:>4} {agi:>4} {sk1:>4} {sk2:>4} {arm:>4} {total_hits:>5} {total_dano:>10.1f} {d_hit:>6.1f}  {sim_str}  {err_str}")

L(f"\n{'='*65}")
L("ADV ATACANDO — dano por hit recebido vs stats adversário")
L(f"{'Perfil':>10} {'Frc':>4} {'Agi':>4} {'Sk1':>4} {'Sk2':>4} {'Arm':>4} {'Hits':>5} {'DanoTotal':>10} {'D/hit':>7} {'SimDano':>8} {'Erro':>7}")
L("-"*80)

por_forca_adv = defaultdict(list)
for r in validos:
    if r["hits_adv"] >= 3 and r["dano_recebido"] > 0 and r["adv_frc"] > 0:
        por_forca_adv[r["adv_frc"]].append(r)

for frc in sorted(por_forca_adv.keys()):
    rs = por_forca_adv[frc]
    total_hits = sum(r["hits_adv"] for r in rs)
    total_dano = sum(r["dano_recebido"] for r in rs)
    d_hit = total_dano / total_hits if total_hits > 0 else 0
    sim_d = sum(r["sim_dano_adv"] for r in rs if r["sim_dano_adv"]) / len(rs) if rs else 0
    sk1 = round(sum(r["adv_s1"] for r in rs)/len(rs))
    sk2 = round(sum(r["adv_s2"] for r in rs)/len(rs))
    arm = round(sum(r["adv_arm"] for r in rs)/len(rs))
    agi = round(sum(r["adv_agi"] for r in rs)/len(rs))
    n = len(rs)
    sim_str = f"{sim_d:>7.1f}" if sim_d > 0 else "       ?"
    err_str = f"{d_hit-sim_d:>+6.1f}" if sim_d > 0 else "      ?"
    L(f"{'('+str(n)+'x)':>10} {frc:>4} {agi:>4} {sk1:>4} {sk2:>4} {arm:>4} {total_hits:>5} {total_dano:>10.1f} {d_hit:>6.1f}  {sim_str}  {err_str}")

# ── Análise de defesa (dano bloqueado) ────────────────────────────────────────
L(f"\n{'='*65}")
L("DEFESA EU — dano bloqueado vs agilidade")
L(f"{'Agi_eu':>7} {'N':>5} {'Blq_total':>10} {'Hits_adv':>9} {'Blq/hit':>8} {'Sim_def':>8} {'Erro':>7}")
L("-"*60)

por_agi = defaultdict(list)
for r in validos:
    if r["hits_adv"] >= 3 and r["dano_bloqueado_eu"] > 0:
        faixa = (r["eu_agi"] // 5) * 5
        por_agi[faixa].append(r)

for agi in sorted(por_agi.keys()):
    rs = por_agi[agi]
    total_hits = sum(r["hits_adv"] for r in rs)
    total_blq  = sum(r["dano_bloqueado_eu"] for r in rs)
    blq_hit = total_blq / total_hits if total_hits > 0 else 0
    sim_d = sum(r["sim_def_eu"] for r in rs if r["sim_def_eu"]) / len(rs) if rs else 0
    n = len(rs)
    sim_str = f"{sim_d:>7.1f}" if sim_d > 0 else "       ?"
    err_str = f"{blq_hit-sim_d:>+6.1f}" if sim_d > 0 else "      ?"
    L(f"{agi:>7}  {n:>5}  {total_blq:>9.1f}  {total_hits:>9}  {blq_hit:>7.2f}  {sim_str}  {err_str}")

# ── Registros detalhados ──────────────────────────────────────────────────────
L(f"\n{'='*65}")
L("DETALHES (100 primeiros com dados de dano):")
L(f"{'Perf':>8} {'Frc':>4} {'Agi':>4} {'Sk2':>4} {'Arm':>4} {'H':>4} {'DanoR':>7} {'D/hit':>6} {'DanoS':>6} {'Err':>5} | {'aFrc':>4} {'aAgi':>4} {'aS2':>4} {'aArm':>4} {'H':>4} {'DanoR':>7} {'D/hit':>6} {'DanoS':>6} {'Err':>5}")
L("-"*115)

com_dados = [r for r in validos if r["hits_eu"] >= 2 and r["dano_causado"] > 0]
for r in com_dados[:100]:
    eu_s = (f"{r['eu_frc']:>4} {r['eu_agi']:>4} {r['eu_s2']:>4} {r['eu_arm']:>4} "
            f"{r['hits_eu']:>4} {r['dano_causado']:>7.1f} "
            f"{r['dano_por_hit_eu']:>6.1f}" if r['dano_por_hit_eu'] else f"{'':>6}")
    sim_eu = f" {r['sim_dano_eu']:>5.1f} {r['erro_dano_eu']:>+5.1f}" if r['sim_dano_eu'] else "     ?     ?"
    adv_s = ""
    if r["hits_adv"] >= 2 and r["dano_recebido"] > 0:
        adv_s = (f" | {r['adv_frc']:>4} {r['adv_agi']:>4} {r['adv_s2']:>4} {r['adv_arm']:>4} "
                 f"{r['hits_adv']:>4} {r['dano_recebido']:>7.1f} "
                 f"{r['dano_por_hit_adv']:>6.1f}" if r['dano_por_hit_adv'] else "")
        sim_adv = f" {r['sim_dano_adv']:>5.1f} {r['erro_dano_adv']:>+5.1f}" if r.get('sim_dano_adv') else ""
        adv_s += sim_adv
    L(f"{r['perfil'][:8]:>8} {eu_s}{sim_eu}{adv_s}")

txt = "\n".join(linhas)
SAIDA_TXT.write_text(txt, encoding="utf-8")
SAIDA.write_text(json.dumps({"total":len(todos),"validos":len(validos),"fontes":fontes,"registros":validos},
    indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n✓ TXT: {SAIDA_TXT}")
print(f"✓ JSON: {SAIDA}")
print(txt)
