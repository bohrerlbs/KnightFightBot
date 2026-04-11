# KnightFight Bot — Contexto do Projeto

## Versao atual: 2.1.66
## GitHub: bohrerlbs/KnightFightBot

## Arquivos principais
- bot.py — bot principal (loop_rapido 2min + loop_lento 1h)
- bot_bg.py — bot BattleGround separado
- combat_sim.py — simulador calibrado (exp=1.8 EU, exp=2.2 ADV)
- launcher.py — servidor HTTP gerenciador de profiles (porta 8764)
- launcher.html — interface web
- profiles/NOME/config.json — config por perfil

## 4 threads daemon no bot.py
1. loop_rapido (120s): gold -> pig -> missao -> imuniza -> taverna
2. loop_lento (3600s): ranking -> pig list -> cache perfis as 3h
3. background (1x): ranking inicial + cache
4. servidor HTTP: dashboard em :PORT

## Logica pig list
- Entra: dp/dd >= 50g OU dprec*10 >= 50g
- Sai: dd>0 e dp==0 (zerou)
- gold_esperado = delta_ouro_perdido / delta_derrotas

## Formula combate calibrada (3530 combates)
- EU: AC^1.8 / (AC^1.8 + Blq^1.8), cap 100% quando ratio >= 3.0
- ADV: AC^2.2 / (AC^2.2 + Blq^2.2)

## Perfil principal
- bohrer (int7, UserID 522001100, Lv22, AC=74, Blq=72)
- ~30 perfis total em varios servers

## Fluxo taverna
- Sempre imuniza antes de entrar se imunidade < 1h
- Usa /job/?filter=1 (so jobs 1-3h)
- Detecta Secondscounter no JS para saber se ja esta em missao
- Apos sair: sair_taverna() + imunizar_agora()
