"""
calibrar_combate.py — Recalibração dos expoentes AC/Bloqueio
Usa todos os combates_srv.json de todos os perfis.

Execução: python calibrar_combate.py
"""

import json
import glob
import math
import sys
import os

# Adiciona o diretório do bot ao path para importar as tabelas
sys.path.insert(0, os.path.dirname(__file__))
from combat_sim import (
    res_to_rounds, melhor_arma, melhor_encaixe, melhor_anel, melhor_amuleto,
    melhor_armadura, melhor_escudo, bonus_forca, bonus_agilidade,
    _ARMAS_1H, _ARMAS_2H,
)


def calcular_score(eu, adv, exp_eu, exp_adv, cap_eu=3.0):
    """
    Calcula score de vitória (0-100) com os expoentes dados.
    Versão simplificada de simular_combate() para calibração.
    """
    eu_lv   = eu.get("level", 1)
    eu_frc  = eu.get("forca", 0)
    eu_res  = eu.get("resistencia", 0)
    eu_agil = eu.get("agilidade", 0)
    eu_ac   = eu.get("arte_combate", 0)
    eu_blq  = eu.get("bloqueio", 0)
    eu_sk1  = eu.get("sk_1mao", 0)
    eu_sk2  = eu.get("sk_2maos", 0)
    eu_arm  = eu.get("sk_armadura", 0)

    adv_lv   = adv.get("level", 1)
    adv_frc  = adv.get("forca", 0)
    adv_res  = adv.get("resistencia", 0)
    adv_agil = adv.get("agilidade", 0)
    adv_ac   = adv.get("arte_combate", 0)
    adv_blq  = adv.get("bloqueio", 0)
    adv_sk1  = adv.get("sk_1mao", 0)
    adv_sk2  = adv.get("sk_2maos", 0)
    adv_arm  = adv.get("sk_armadura", 0)

    # Anéis + amuleto
    eu_anel  = melhor_anel(eu_lv);  eu_amu  = melhor_amuleto(eu_lv)
    adv_anel = melhor_anel(adv_lv); adv_amu = melhor_amuleto(adv_lv)
    eu_frc  += eu_anel[0]*2  + eu_amu[0];  eu_agil += eu_anel[1]*2  + eu_amu[1]
    eu_res  += eu_anel[2]*2  + eu_amu[2];  eu_ac   += eu_anel[3]*2  + eu_amu[3]
    eu_blq  += eu_anel[4]*2  + eu_amu[4]
    adv_frc += adv_anel[0]*2 + adv_amu[0]; adv_agil += adv_anel[1]*2 + adv_amu[1]
    adv_res += adv_anel[2]*2 + adv_amu[2]; adv_ac   += adv_anel[3]*2 + adv_amu[3]
    adv_blq += adv_anel[4]*2 + adv_amu[4]

    # Arma
    eu_arma  = melhor_arma(eu_sk2,  _ARMAS_2H) if eu_sk2  > eu_sk1  else melhor_arma(eu_sk1,  _ARMAS_1H)
    adv_arma = melhor_arma(adv_sk2, _ARMAS_2H) if adv_sk2 > adv_sk1 else melhor_arma(adv_sk1, _ARMAS_1H)

    eu_dano_base  = (eu_arma[0]  + eu_arma[1])  / 2
    adv_dano_base = (adv_arma[0] + adv_arma[1]) / 2
    adv_res  = max(0, adv_res  + adv_arma[3])
    adv_agil = adv_agil + adv_arma[4]

    # Encaixes
    eu_dano_total  = eu_dano_base  + bonus_forca(eu_dano_base,  eu_frc)  + melhor_encaixe(eu_lv)  * eu_arma[2]
    adv_dano_total = adv_dano_base + bonus_forca(adv_dano_base, adv_frc) + melhor_encaixe(adv_lv) * adv_arma[2]

    # Defesa
    eu_arm_def  = melhor_armadura(eu_arm)
    eu_esc_def  = melhor_escudo(eu_arm) if eu_sk1 >= eu_sk2 else (0, 0, 0)
    eu_def      = (eu_arm_def[0]+eu_arm_def[1])/2 + (eu_esc_def[0]+eu_esc_def[1])/2
    eu_def     += bonus_agilidade(eu_def, eu_agil)
    eu_blq     += eu_esc_def[2] if len(eu_esc_def) > 2 else 0

    adv_arm_def = melhor_armadura(adv_arm)
    adv_esc_def = melhor_escudo(adv_arm) if adv_sk1 >= adv_sk2 else (0, 0, 0)
    adv_def     = (adv_arm_def[0]+adv_arm_def[1])/2 + (adv_esc_def[0]+adv_esc_def[1])/2
    adv_def    += bonus_agilidade(adv_def, adv_agil)
    adv_blq    += adv_esc_def[2] if len(adv_esc_def) > 2 else 0

    # Taxa de acerto
    if eu_ac > 0 and adv_blq > 0:
        ratio = eu_ac / adv_blq
        taxa_eu = 1.0 if ratio >= cap_eu else eu_ac**exp_eu / (eu_ac**exp_eu + adv_blq**exp_eu)
    else:
        taxa_eu = 0.5 if eu_ac > 0 else 0.1

    if adv_ac > 0 and eu_blq > 0:
        taxa_adv = adv_ac**exp_adv / (adv_ac**exp_adv + eu_blq**exp_adv)
    else:
        taxa_adv = 0.5 if adv_ac > 0 else 0.1

    # Rounds e dano líquido
    rounds_eu  = res_to_rounds(eu_res)
    rounds_adv = res_to_rounds(adv_res)
    total_eu   = max(0, eu_dano_total  - adv_def) * taxa_eu  * rounds_eu
    total_adv  = max(0, adv_dano_total - eu_def)  * taxa_adv * rounds_adv

    if total_eu + total_adv == 0:
        return 50.0
    return min(100.0, max(0.0, total_eu / (total_eu + total_adv) * 100))


def carregar_dados():
    registros = []
    for f in glob.glob("profiles/*/combates_srv.json"):
        try:
            d = json.load(open(f))
            for r in d:
                if (r.get("eu_ac", 0) > 5 and r.get("eu_blq", 0) > 5 and
                        r.get("adv_ac", 0) > 5 and r.get("adv_blq", 0) > 5 and
                        r.get("eu_res", 0) > 0 and r.get("adv_res", 0) > 0 and
                        r.get("resultado") in ("vitoria", "derrota")):
                    registros.append(r)
        except Exception:
            pass
    return registros


def brier_score(registros, exp_eu, exp_adv, cap_eu=3.0):
    """Brier score = MSE entre probabilidade prevista e resultado binário."""
    total = 0.0
    for r in registros:
        eu = {
            "level": r.get("eu_lv", 1), "forca": r.get("eu_frc", 0),
            "resistencia": r.get("eu_res", 0), "agilidade": r.get("eu_agil", 0),
            "arte_combate": r.get("eu_ac", 0), "bloqueio": r.get("eu_blq", 0),
            "sk_1mao": r.get("eu_s1", 0), "sk_2maos": r.get("eu_s2", 0),
            "sk_armadura": r.get("eu_arm", 0),
        }
        adv = {
            "level": r.get("adv_lv", 1), "forca": r.get("adv_frc", 0),
            "resistencia": r.get("adv_res", 0), "agilidade": r.get("adv_agil", 0),
            "arte_combate": r.get("adv_ac", 0), "bloqueio": r.get("adv_blq", 0),
            "sk_1mao": r.get("adv_s1", 0), "sk_2maos": r.get("adv_s2", 0),
            "sk_armadura": r.get("adv_arm", 0),
        }
        score = calcular_score(eu, adv, exp_eu, exp_adv, cap_eu)
        prob = score / 100.0
        real = 1.0 if r["resultado"] == "vitoria" else 0.0
        total += (prob - real) ** 2
    return total / len(registros)


def main():
    print("Carregando combates...")
    registros = carregar_dados()
    print("Registros carregados: %d" % len(registros))
    print()

    # ── Baseline com parâmetros atuais ──────────────────────────────────────────
    bs_atual = brier_score(registros, 1.8, 2.2, 3.0)
    print("Baseline (exp_eu=1.8, exp_adv=2.2, cap=3.0): Brier=%.5f" % bs_atual)
    print()

    # ── Grid search ─────────────────────────────────────────────────────────────
    # exp_eu: 1.2 a 3.0 (passo 0.1)
    # exp_adv: 1.5 a 3.5 (passo 0.1)
    # cap_eu: 2.0 a 5.0 (passo 0.5)
    melhor_bs   = bs_atual
    melhor_eu   = 1.8
    melhor_adv  = 2.2
    melhor_cap  = 3.0

    exp_eu_range  = [x/10 for x in range(12, 31)]   # 1.2 a 3.0
    exp_adv_range = [x/10 for x in range(15, 36)]   # 1.5 a 3.5
    cap_range     = [x/2  for x in range(4, 11)]    # 2.0 a 5.0

    total_iter = len(exp_eu_range) * len(exp_adv_range) * len(cap_range)
    print("Grid search: %d combinações..." % total_iter)

    i = 0
    for exp_eu in exp_eu_range:
        for exp_adv in exp_adv_range:
            for cap in cap_range:
                bs = brier_score(registros, exp_eu, exp_adv, cap)
                if bs < melhor_bs:
                    melhor_bs  = bs
                    melhor_eu  = exp_eu
                    melhor_adv = exp_adv
                    melhor_cap = cap
                i += 1
                if i % 200 == 0:
                    print("  %d/%d... melhor até agora: eu=%.1f adv=%.1f cap=%.1f Brier=%.5f" % (
                        i, total_iter, melhor_eu, melhor_adv, melhor_cap, melhor_bs))

    print()
    print("=" * 60)
    print("RESULTADO DA CALIBRAÇÃO")
    print("=" * 60)
    print("Baseline : exp_eu=1.8  exp_adv=2.2  cap=3.0  Brier=%.5f" % bs_atual)
    print("Otimizado: exp_eu=%.1f  exp_adv=%.1f  cap=%.1f  Brier=%.5f" % (
        melhor_eu, melhor_adv, melhor_cap, melhor_bs))
    melhora = (bs_atual - melhor_bs) / bs_atual * 100
    print("Melhora : %.2f%%" % melhora)
    print()

    # ── Análise por faixa de score com os parâmetros otimizados ─────────────────
    print("Validação com parâmetros otimizados (score_previsto vs real):")
    print("bucket | registros | vitoria_real | vitoria_esperada")
    buckets = {}
    for r in registros:
        eu = {
            "level": r.get("eu_lv", 1), "forca": r.get("eu_frc", 0),
            "resistencia": r.get("eu_res", 0), "agilidade": r.get("eu_agil", 0),
            "arte_combate": r.get("eu_ac", 0), "bloqueio": r.get("eu_blq", 0),
            "sk_1mao": r.get("eu_s1", 0), "sk_2maos": r.get("eu_s2", 0),
            "sk_armadura": r.get("eu_arm", 0),
        }
        adv = {
            "level": r.get("adv_lv", 1), "forca": r.get("adv_frc", 0),
            "resistencia": r.get("adv_res", 0), "agilidade": r.get("adv_agil", 0),
            "arte_combate": r.get("adv_ac", 0), "bloqueio": r.get("adv_blq", 0),
            "sk_1mao": r.get("adv_s1", 0), "sk_2maos": r.get("adv_s2", 0),
            "sk_armadura": r.get("adv_arm", 0),
        }
        sc = int(calcular_score(eu, adv, melhor_eu, melhor_adv, melhor_cap))
        b = (sc // 10) * 10
        if b not in buckets:
            buckets[b] = [0, 0]
        buckets[b][0] += 1
        if r["resultado"] == "vitoria":
            buckets[b][1] += 1

    for b in sorted(buckets.keys()):
        tot, vit = buckets[b]
        esp = b + 5
        print("%3d-%3d%% | %6d    | %5.1f%%       | %5.1f%%" % (
            b, b+9, tot, 100*vit/tot, esp))


if __name__ == "__main__":
    main()
