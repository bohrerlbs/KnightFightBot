"""
export_modelo.py — Ferramenta do desenvolvedor
===============================================
Consolida combates de todos os perfis e gera:
  - modelo_combate.json    (servidor normal)
  - modelo_combate_bg.json (BattleGround)

Uso:
    python export_modelo.py
    python export_modelo.py --ver   (só mostra stats, não salva)
    python export_modelo.py --pasta C:\\caminho\\kfbot
"""

import json, sys, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict


def carregar_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except:
        return []


def gerar_modelo_srv(combates):
    """Gera modelo do servidor normal."""
    if len(combates) < 10:
        return {}
    total    = len(combates)
    vitorias = sum(1 for c in combates if c.get("resultado") == "vitoria")

    # WR por hit rate
    wr_hr = defaultdict(lambda: {"v":0,"t":0})
    for c in combates:
        eu_ac, adv_blq = c.get("eu_ac",0), c.get("adv_blq",0)
        if eu_ac > 0:
            taxa = round(eu_ac/(eu_ac+adv_blq)*10)/10 if adv_blq > 0 else 1.0
            wr_hr[f"{taxa:.1f}"]["t"] += 1
            if c.get("resultado") == "vitoria":
                wr_hr[f"{taxa:.1f}"]["v"] += 1

    # WR por delta level
    wr_lv = defaultdict(lambda: {"v":0,"t":0})
    for c in combates:
        delta = str(max(-5, min(10, c.get("adv_level",0) - c.get("eu_level",0))))
        wr_lv[delta]["t"] += 1
        if c.get("resultado") == "vitoria":
            wr_lv[delta]["v"] += 1

    return {
        "gerado_em": datetime.now().isoformat(),
        "total": total,
        "vitorias": vitorias,
        "win_rate": round(vitorias/total*100, 1),
        "wr_hitrate": {k:{"wr":round(v["v"]/v["t"]*100,1),"n":v["t"]}
                       for k,v in wr_hr.items() if v["t"]>=3},
        "wr_delta_lv": {k:{"wr":round(v["v"]/v["t"]*100,1),"n":v["t"]}
                        for k,v in wr_lv.items() if v["t"]>=3},
    }


def gerar_modelo_bg(combates):
    """Gera modelo do BattleGround."""
    if len(combates) < 5:
        return {}
    total    = len(combates)
    vitorias = sum(1 for c in combates if c.get("resultado") == "vitoria")

    # WR por faixa de EF do adversário
    wr_ef = defaultdict(lambda: {"v":0,"t":0})
    for c in combates:
        ef = c.get("adv_ef", c.get("ef_adv", 0))
        faixa = f"{round(ef*2)/2:.1f}"  # arredonda para 0.5
        wr_ef[faixa]["t"] += 1
        if c.get("resultado") == "vitoria":
            wr_ef[faixa]["v"] += 1

    # WR por tipo (zumbi vs humano)
    wr_tipo = defaultdict(lambda: {"v":0,"t":0})
    for c in combates:
        tipo = c.get("adv_tipo", "?")
        wr_tipo[tipo]["t"] += 1
        if c.get("resultado") == "vitoria":
            wr_tipo[tipo]["v"] += 1

    return {
        "gerado_em": datetime.now().isoformat(),
        "total": total,
        "vitorias": vitorias,
        "win_rate": round(vitorias/total*100, 1),
        "wr_por_ef": {k:{"wr":round(v["v"]/v["t"]*100,1),"n":v["t"]}
                      for k,v in wr_ef.items() if v["t"]>=2},
        "wr_por_tipo": {k:{"wr":round(v["v"]/v["t"]*100,1),"n":v["t"]}
                        for k,v in wr_tipo.items() if v["t"]>=2},
    }


def imprimir_modelo_srv(modelo, combates_por_perfil):
    print(f"\n  Win Rate geral:  {modelo['win_rate']}%")
    print(f"  Total combates:  {modelo['total']}")
    if modelo.get("wr_hitrate"):
        print("\n  WR por Hit Rate:")
        for k in sorted(modelo["wr_hitrate"], key=float):
            d = modelo["wr_hitrate"][k]
            bar = "#" * int(d["wr"]/5)
            print(f"    HR {k}: {d['wr']:>5}%  {bar:<20} ({d['n']} combates)")
    if modelo.get("wr_delta_lv"):
        print("\n  WR por Delta Level:")
        for k in sorted(modelo["wr_delta_lv"], key=int):
            d = modelo["wr_delta_lv"][k]
            lbl = f"+{k}" if int(k)>0 else k
            bar = "#" * int(d["wr"]/5)
            print(f"    Lv {lbl:>3}: {d['wr']:>5}%  {bar:<20} ({d['n']} combates)")


def imprimir_modelo_bg(modelo):
    print(f"\n  Win Rate geral:  {modelo['win_rate']}%")
    print(f"  Total batalhas:  {modelo['total']}")
    if modelo.get("wr_por_ef"):
        print("\n  WR por EF do adversário:")
        for k in sorted(modelo["wr_por_ef"], key=float):
            d = modelo["wr_por_ef"][k]
            bar = "#" * int(d["wr"]/5)
            print(f"    EF {k}: {d['wr']:>5}%  {bar:<20} ({d['n']} batalhas)")
    if modelo.get("wr_por_tipo"):
        print("\n  WR por tipo:")
        for k, d in modelo["wr_por_tipo"].items():
            print(f"    {k:<15}: {d['wr']}% ({d['n']} batalhas)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pasta", default=None)
    parser.add_argument("--ver", action="store_true")
    args = parser.parse_args()

    base = Path(args.pasta) if args.pasta else Path(__file__).parent
    profiles_dir = base / "profiles"

    if not profiles_dir.exists():
        print(f"ERRO: pasta profiles nao encontrada em {base}")
        sys.exit(1)

    print("=" * 60)
    print("  KnightFight Bot — Exportador de Modelos")
    print("=" * 60)

    # ── Coleta combates ────────────────────────────────────────
    todos_srv = []
    todos_bg  = []
    print("\n  Perfil                     Servidor  BG")
    print("  " + "-"*45)

    for d in sorted(profiles_dir.iterdir()):
        if not d.is_dir(): continue
        srv = carregar_json(d / "combates_srv.json")
        bg  = carregar_json(d / "bg_combates.json")
        if srv or bg:
            todos_srv.extend(srv)
            todos_bg.extend(bg)
            v_srv = sum(1 for c in srv if c.get("resultado")=="vitoria")
            v_bg  = sum(1 for c in bg  if c.get("resultado")=="vitoria")
            wr_srv = f"{round(v_srv/len(srv)*100)}%" if srv else "—"
            wr_bg  = f"{round(v_bg/len(bg)*100)}%"  if bg  else "—"
            print(f"  {d.name:<28} {len(srv):>4} ({wr_srv})  {len(bg):>4} ({wr_bg})")

    print(f"\n  TOTAL: {len(todos_srv)} combates servidor | {len(todos_bg)} batalhas BG")

    # ── Servidor normal ────────────────────────────────────────
    print("\n" + "─"*60)
    print("  SERVIDOR NORMAL")
    print("─"*60)
    modelo_srv = gerar_modelo_srv(todos_srv)
    if modelo_srv:
        imprimir_modelo_srv(modelo_srv, {})
    else:
        print("  Combates insuficientes.")

    # ── BattleGround ───────────────────────────────────────────
    print("\n" + "─"*60)
    print("  BATTLEGROUND")
    print("─"*60)
    modelo_bg = gerar_modelo_bg(todos_bg)
    if modelo_bg:
        imprimir_modelo_bg(modelo_bg)
    else:
        print("  Batalhas insuficientes.")

    print("\n" + "="*60)

    if args.ver:
        print("  [--ver] Arquivos NAO salvos.")
        return

    # ── Salva ──────────────────────────────────────────────────
    if modelo_srv:
        out = base / "modelo_combate.json"
        out.write_text(json.dumps(modelo_srv, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  Salvo: {out}")

    if modelo_bg:
        out = base / "modelo_combate_bg.json"
        out.write_text(json.dumps(modelo_bg, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  Salvo: {out}")

    print()
    print("  Proximo passo:")
    print("  git add modelo_combate.json modelo_combate_bg.json")
    print("  git commit -m 'modelo atualizado'")
    print("  git push")
    print("="*60)


if __name__ == "__main__":
    main()
