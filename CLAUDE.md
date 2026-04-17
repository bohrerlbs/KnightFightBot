# KnightFight Bot — Contexto do Projeto

## Versao atual: 2.3.2
## GitHub: bohrerlbs/KnightFightBot

## Arquivos principais
- bot.py — bot principal
- bot_bg.py — bot BattleGround separado
- combat_sim.py — simulador calibrado (exp=1.8 EU, exp=2.2 ADV)
- launcher.py — servidor HTTP gerenciador de profiles (porta 8764)
- launcher.html — interface web
- profiles/NOME/config.json — config por perfil

## 5 threads daemon no bot.py
1. loop_acoes: dorme durante taverna/CD, acorda livre -> scan lojas -> compras -> HP -> treino -> pig/imuniza/missao
2. loop_ranking (3600s): scrape ranking + pig list delta — nunca bloqueia por taverna/missao
3. loop_lento (3600s): status + atributos + cache perfis as 3h
4. background (1x): ranking inicial + cache
5. servidor HTTP: dashboard em :PORT

## Logica loop_acoes
- Topo do ciclo: verifica taverna (dorme seg_restante+30) e CD (dorme seg_cd+10)
- Quando livre: scan lojas -> compras -> HP/altar -> treino -> pig/imuniza/missao
- Garante que item_alvo esta sempre fresco quando o personagem esta disponivel

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

## Logica upgrade equipamento
- waffen/schilde/ruestungen: req_skill_equipado < req_skill_loja E req_skill_loja <= skill_personagem -> compra
- aneis/amuletos: req_level_equipado < req_level_loja E req_level_loja <= level_personagem -> compra
- Contagem de aneis via secao inventario (qty_total real, nao por TR sell-link)
