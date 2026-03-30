"""
analisar_combates.py
Compila combates de TODOS os perfis encontrados recursivamente.

Uso:
    python analisar_combates.py                    # busca em pasta atual
    python analisar_combates.py C:\\kfbot           # busca em C:\\kfbot
"""

import json, pathlib, sys, math
from collections import defaultdict

BASE  = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(".")
SAIDA     = BASE / "analise_combates.json"
SAIDA_TXT = BASE / "analise_combates.txt"
ARQUIVOS  = ["combates_srv.json", "bg_combates.json"]

todos, fontes = [], []

for arq in ARQUIVOS:
    for f in sorted(BASE.rglob(arq)):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                perfil = f.parent.name
                for c in data:
                    c["_fonte"]  = arq
                    c["_perfil"] = perfil
                todos.extend(data)
                fontes.append(f"  {perfil}/{arq} ({len(data)} combates)")
        except Exception as e:
            print(f"  ERRO {f}: {e}")

print(f"\n{'='*60}")
print(f"ARQUIVOS: {len(fontes)}")
for f in fontes: print(f)
print(f"TOTAL: {len(todos)} combates")

com_turnos = [c for c in todos if c.get("hits_eu",0)+c.get("misses_eu",0) > 0]
print(f"Com turnos: {len(com_turnos)}")

if not com_turnos:
    print("Sem dados de turnos — use v1.7.5+"); sys.exit(0)

def taxa_sim(ac, blq):
    return ac/(ac+blq)*100 if (ac+blq)>0 else 50

registros = []
for c in com_turnos:
    ea,eb = c.get("eu_ac",0), c.get("eu_blq",0)
    aa,ab = c.get("adv_ac",0), c.get("adv_blq",0)
    he,me = c.get("hits_eu",0), c.get("misses_eu",0)
    ha,ma = c.get("hits_adv",0), c.get("misses_adv",0)
    te, ta = he+me, ha+ma
    if te < 2 or ea==0 or ab==0: continue
    re = he/te*100
    ra = ha/ta*100 if ta>=2 else None
    se, sa = taxa_sim(ea,ab), taxa_sim(aa,eb) if aa>0 else None
    registros.append({
        "perfil":c.get("_perfil","?"), "fonte":c.get("_fonte","?"),
        "eu_ac":ea,"eu_blq":eb,"adv_ac":aa,"adv_blq":ab,
        "hits_eu":he,"misses_eu":me,"total_eu":te,
        "hits_adv":ha,"misses_adv":ma,"total_adv":ta,
        "taxa_real_eu":round(re,1),"taxa_sim_eu":round(se,1),"erro_eu":round(re-se,1),
        "taxa_real_adv":round(ra,1) if ra is not None else None,
        "taxa_sim_adv":round(sa,1)  if sa is not None else None,
        "erro_adv":round(ra-sa,1) if (ra and sa) else None,
        "dano_bloqueado_eu":c.get("dano_bloqueado_eu",0),
    })

print(f"Válidos: {len(registros)}")

beu  = defaultdict(list)
badv = defaultdict(list)
reu  = defaultdict(list)
radv = defaultdict(list)

for r in registros:
    k = round(r["taxa_sim_eu"]/5)*5
    beu[k].append(r["taxa_real_eu"])
    if r["taxa_real_adv"] and r["total_adv"]>=2:
        k2 = round(r["taxa_sim_adv"]/5)*5
        badv[k2].append(r["taxa_real_adv"])
    if r["adv_blq"]>0:
        ratio = round(r["eu_ac"]/r["adv_blq"]*4)/4
        reu[ratio].append(r["taxa_real_eu"])
    if r["eu_blq"]>0 and r["taxa_real_adv"] and r["total_adv"]>=2:
        ratio2 = round(r["adv_ac"]/r["eu_blq"]*4)/4
        radv[ratio2].append(r["taxa_real_adv"])

def rmse(f, pares):
    if not pares: return 999
    return (sum((f(a,b)-re)**2 for a,b,re,n in pares)/len(pares))**0.5

pares_eu  = [(r["eu_ac"],r["adv_blq"],r["taxa_real_eu"],r["total_eu"]) for r in registros]
pares_adv = [(r["adv_ac"],r["eu_blq"],r["taxa_real_adv"],r["total_adv"]) for r in registros
             if r["taxa_real_adv"] and r["total_adv"]>=2 and r["adv_ac"]>0]

formulas = [
    ("AC/(AC+Blq) — atual",     lambda a,b: a/(a+b)*100),
    ("AC^1.2/(AC^1.2+Blq^1.2)",lambda a,b: a**1.2/(a**1.2+b**1.2)*100),
    ("AC^1.3/(AC^1.3+Blq^1.3)",lambda a,b: a**1.3/(a**1.3+b**1.3)*100),
    ("AC^1.5/(AC^1.5+Blq^1.5)",lambda a,b: a**1.5/(a**1.5+b**1.5)*100),
    ("Amigo: ratio/1.75*100",   lambda a,b: min(100,max(0,(a/b)/1.75*100))),
]

linhas = []
L = linhas.append
L(f"\nANÁLISE DE COMBATES — KnightFight Bot")
L("="*65)
L(f"Arquivos encontrados: {len(fontes)}")
for f in fontes: L(f)
L(f"Total: {len(todos)} | Com turnos: {len(com_turnos)} | Válidos: {len(registros)}")

L(f"\n{'='*65}")
L("RMSE por fórmula (menor = melhor):")
L(f"{'Fórmula':<40} {'EU':>7} {'ADV':>8}")
L("-"*58)
for nome, f in formulas:
    L(f"{nome:<40} {rmse(f,pares_eu):>6.2f}%  {rmse(f,pares_adv):>6.2f}%")

for titulo, buck, label in [
    ("EU ATACANDO — por bucket sim%", beu, "Sim%"),
    ("ADV ATACANDO — por bucket sim%", badv, "Sim%"),
]:
    L(f"\n{'='*65}")
    L(titulo)
    L(f"{'Sim%':>6} {'N':>5} {'Real%':>9} {'Erro':>8}")
    L("-"*32)
    erros = []
    for k in sorted(buck):
        v = buck[k]
        if len(v)<3: continue
        m = sum(v)/len(v)
        e = m-k
        erros.append(e)
        L(f"{k:>5}%  {len(v):>5}  {m:>8.1f}%  {e:>+7.1f}%")
    if erros: L(f"{'Erro médio global:':>32} {sum(erros)/len(erros):>+.1f}%")

for titulo, buck, quem in [
    ("EU ATACANDO — por razão AC/Blq_adv", reu, "eu"),
    ("ADV ATACANDO — por razão AC_adv/Blq_eu", radv, "adv"),
]:
    L(f"\n{'='*65}")
    L(titulo)
    L(f"{'Ratio':>7} {'N':>5} {'Real%':>9} {'Sim%':>8} {'Erro':>8}")
    L("-"*42)
    for k in sorted(buck):
        v = buck[k]
        if len(v)<3: continue
        m = sum(v)/len(v)
        sim = k/(1+k)*100
        L(f"{k:>6.2f}  {len(v):>5}  {m:>8.1f}%  {sim:>7.1f}%  {m-sim:>+7.1f}%")

L(f"\n{'='*65}")
L("DETALHES (100 combates, maior ratio primeiro):")
L(f"{'Perf':>8} {'EUac':>5} {'Ablq':>5} {'H':>4} {'M':>4} {'Real':>6} {'Sim':>5} {'Err':>5} | {'Aac':>4} {'Eblq':>5} {'H':>4} {'M':>4} {'Real':>6} {'Sim':>5} {'Err':>5}")
L("-"*95)
sr = sorted(registros, key=lambda r: r["eu_ac"]/(r["adv_blq"] or 1), reverse=True)
for r in sr[:100]:
    eu_s = f"{r['eu_ac']:>5} {r['adv_blq']:>5} {r['hits_eu']:>4} {r['misses_eu']:>4} {r['taxa_real_eu']:>5.1f}% {r['taxa_sim_eu']:>4.1f}% {r['erro_eu']:>+4.1f}%"
    adv_s = ""
    if r["taxa_real_adv"] is not None and r["taxa_sim_adv"] is not None and r["erro_adv"] is not None:
        adv_s = f" | {r['adv_ac']:>4} {r['eu_blq']:>5} {r['hits_adv']:>4} {r['misses_adv']:>4} {r['taxa_real_adv']:>5.1f}% {r['taxa_sim_adv']:>4.1f}% {r['erro_adv']:>+4.1f}%"
    L(f"{r['perfil'][:8]:>8} {eu_s}{adv_s}")

txt = "\n".join(linhas)
SAIDA_TXT.write_text(txt, encoding="utf-8")
SAIDA.write_text(json.dumps({"total":len(todos),"com_turnos":len(com_turnos),"validos":len(registros),
    "fontes":fontes,
    "buckets_eu":{str(k):{"n":len(v),"real":round(sum(v)/len(v),1),"sim":k} for k,v in beu.items() if len(v)>=3},
    "buckets_adv":{str(k):{"n":len(v),"real":round(sum(v)/len(v),1),"sim":k} for k,v in badv.items() if len(v)>=3},
    "ratio_eu":{str(k):{"n":len(v),"real":round(sum(v)/len(v),1),"sim":round(k/(1+k)*100,1)} for k,v in reu.items() if len(v)>=3},
    "ratio_adv":{str(k):{"n":len(v),"real":round(sum(v)/len(v),1),"sim":round(k/(1+k)*100,1)} for k,v in radv.items() if len(v)>=3},
    "registros":registros},indent=2,ensure_ascii=False),encoding="utf-8")

print(f"\n✓ TXT: {SAIDA_TXT}")
print(f"✓ JSON: {SAIDA}")
print(txt)
