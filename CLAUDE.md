# KnightFight Bot — Contexto do Projeto

## Versao atual: 2.3.62
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

## Deteccao automatica de reset de personagem (v2.3.60+)
- No inicio do modo "loop", compara level ao vivo (/status/) com level salvo em estado.json
- Se level_live < level_local -> reset confirmado (level nunca regride no jogo)
- tratar_reset_personagem() faz backup de estado.json/pig_list.json/bg_estado.json etc em
  _backup_personagem_antigo_<timestamp>/ e reloga via fazer_login_moonid() usando
  game_user/game_pass do config.json, atualizando cookies+userid automaticamente
- Se faltar game_user/game_pass ou o relogin falhar (ex: conta banida de verdade), o bot
  para com sys.exit(1) em vez de rodar quebrado
- So roda no bot.py; bot_bg.py nao tem login proprio, depende do cookie que o bot.py grava
  em config.json

## HTTP 418 (v2.3.61+) — cookie preso a userid antigo
- KFClient.get/post chamam r.raise_for_status() -> 418 vira requests.exceptions.HTTPError,
  NAO SessaoExpiradaError (essa so cobre redirect pra /login), entao o catch de sessao
  vencida nunca pegava 418
- 2 pontos cobertos: (1) /status/ inicial no arranque do modo loop -> se 418, chama
  tratar_reset_personagem() direto (nem tenta comparar level, pois o proprio /status/
  falhou); (2) dentro do loop_acoes, novo "except requests.exceptions.HTTPError" antes do
  "except Exception" generico -> chama renovar_cookie_auto() e continua. Antes dessa
  correcao, um 418 em pleno loop caia no except Exception generico que so loga e dorme
  pra sempre (bot fica "parado" sem nunca relogar) — foi o que aconteceu no reset de
  2026-07-01, a deteccao por level nao ajudou pq o /status/ em si ja dava 418
- renovar_cookie_auto() agora retorna dict {"cookie","userid"} (antes so retornava a
  string do cookie) e atualiza userid no config.json tambem, nao so cookie

## Taverna curta vs taverna inteligente (v2.3.62+)
- Prioridade correta: pig > missao > taverna. TAVERNA_INTELIGENTE (taverna longa, ate
  12h/ate HORARIO_PARADA) so deve rodar quando realmente nao ha pig nem missao (cota_diaria
  confirmada por gerenciar_missao com gold >= 10g)
- Bug corrigido: os 3 pontos do loop_acoes que caiam em "sem gold" (gold<5g pre-pig,
  gold<10g apos loop de pig sem alvo, gold<10g apos imunizar) chamavam _entrar_taverna()
  direto -> com TAVERNA_INTELIGENTE ligado isso sempre virava sessao longa (12h), mesmo
  quando o unico problema era falta de gold pontual (nao falta real de pig/missao) — foi o
  caso do reset de personagem em 2026-07-01: personagem novo com pouco gold entrou 12h de
  taverna quando so precisava de 1h pra levantar gold e voltar pras missoes
- Agora esses 3 pontos chamam _taverna_1h(client) direto (ignora TAVERNA_INTELIGENTE,
  sempre 1h curta) — bot.py:6357, 6581, 6614. O terceiro ponto legitimo (cota_diaria
  confirmada, bot.py:6603 e 6627) continua usando _entrar_taverna() (taverna
  longa/inteligente quando aplicavel)
