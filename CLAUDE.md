# KnightFight Bot — Contexto do Projeto

## Versao atual: 2.3.68
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

## Login automatico moonid.net exige header Origin (v2.3.63+)
- fazer_login_moonid() (bot.py:42) fazia POST em https://moonid.net/account/login/ so com
  header Referer -> moonid.net passou a exigir tambem Origin: https://moonid.net na
  checagem CSRF do Django; sem isso o POST leva 403 "CSRF verification failed"
- O codigo antigo so checava "/account/login/" in r2.url pra decidir se foi erro de senha,
  entao um 403 CSRF virava "usuario ou senha invalidos" mesmo com credenciais corretas —
  foi o caso do perfil btt_br2 (R4tazana/Rat225@, mesma conta do razar_br2, senha correta
  confirmada manualmente pelo usuario)
- Fix: adiciona Origin: https://moonid.net ao POST + checa r2.status_code == 403
  separadamente pra dar mensagem de erro correta (nao mascarar como senha invalida)

## Modal "Novo Perfil" — captura de cookie via Selenium era o fluxo errado (v2.3.65)
- LEIA-ME.txt do zip de distribuicao manda todo mundo logar no launcher com
  admin/admin123 (unico login compartilhado) -> _myRole sempre vira 'admin' no JS ->
  openNewProfileModal() nunca esconde 'new-capture-box' (esse esconderijo so vale pra
  usuarios nao-admin, ver adminOnlyIds em launcher.html:1004)
- Resultado: todo amigo que abria "Novo Perfil" via pelo topo do modal o botao
  "[web] Abrir browser e capturar cookie automaticamente", que depende de Selenium
  (launcher.py:589, capture_cookie_browser) — mesmo sem precisar disso, ja que o
  fluxo recomendado (usuario/senha do moonid.net, sem Selenium, login via requests em
  fazer_login_moonid) fica logo abaixo
- Fix: moveu o capture-box pra depois do campo de cookie manual, renomeou pra
  "Avançado (opcional)" deixando claro que requer Selenium; instalar_dependencias.bat
  agora verifica com `python -c "import ... selenium ..."` se a instalacao realmente
  funcionou (antes dizia "OK - Dependencias instaladas!" mesmo quando pip falhava
  silenciosamente, ex: --quiet escondia o erro e nunca havia checagem pos-instalacao)
- release/KnightFightBot.zip reconstruido manualmente (nao ha script de build pro zip;
  lista de arquivos replicada a mao — ver histórico do commit "pacote release")

## instalar_dependencias.bat — "pip nao reconhecido" mesmo com Python OK (v2.3.66)
- Python 3.14 instalado via python.org com "Add to PATH" as vezes linka python.exe mas
  nao Scripts\ (onde fica pip.exe) -> `python --version` funciona mas `pip install...`
  solto da erro "'pip' nao e reconhecido como um comando interno ou externo" mesmo com
  Python instalado corretamente — foi o caso de um amigo tentando rodar o instalador
- Fix: troca `pip install ...` por `python -m pip install ...` em todos os pontos do
  instalar_dependencias.bat (inclusive na mensagem de erro manual) — invoca o pip como
  modulo do proprio interpretador Python ja resolvido, sem depender do PATH do pip.exe
- Tambem removido o em dash "—" do titulo do echo (trocado por hifen "-"): esse
  caractere multi-byte UTF-8 combinado com `chcp 65001` estava corrompendo a saida do
  console em alguns PCs (aparecia `'ho' nao e reconhecido...` no topo do output, em vez
  do titulo)
- release/KnightFightBot.zip atualizado com o instalar_dependencias.bat corrigido (sem
  script de build, arquivo substituido direto dentro do zip via python zipfile)

## Bloqueio de IP pelo CloudFront — 403 "Request blocked" (v2.3.67)
- Sintoma: bot loga "Falha ao renovar cookie: URL de login nao encontrada no servidor 'X'" e o
  dashboard/launcher mostra "cookie vencido" mesmo com game_user/game_pass certos. Nao e
  login: o host do jogo (*.knightfight.moonid.net) devolve HTTP 403 do CloudFront ("The request
  could not be satisfied / Request blocked") pra QUALQUER pagina, ate no Chrome e ate com cookie
  valido — o IP da maquina foi bloqueado. moonid.net/account/login/ continua 200. Confirmado em
  2026-09-21 (IP 201.10.41.197): abrir o jogo por VPN funcionou. Os primeiros 403 no bot.log
  apareceram em 2026-09-20 20:59, quando ~11 perfis iniciaram juntos (ranking + cache de perfis
  + scans de loja), tudo no mesmo IP. O limite exato do WAF nao e conhecido
- Por que enganava: KFClient chama raise_for_status(), entao 403 virava HTTPError -> o handler
  do loop_acoes (v2.3.61, pensado pro 418) rodava renovar_cookie_auto() -> mais requisicoes ao
  host bloqueado -> falhava no passo 1 do fazer_login_moonid -> marcava status_bot
  "cookie_expirado" e dormia 3600s
- Fix: classe _KFSession(requests.Session) (bot.py, antes de fazer_login_moonid) usada em
  KFClient e no login. Em toda requisicao: (1) se ha bloqueio conhecido, ESPERA o fim da janela
  (login usa esperar_bloqueio=False e levanta BloqueioIPError na hora); (2) limite de taxa de
  0.2s entre requisicoes por processo (RATE_MIN_INTERVALO_SEG); (3) 403 cujo corpo tem "the
  request could not be satisfied" -> BloqueioIPError + janela de backoff 15min/30min/1h
  (BLOQUEIO_IP_BACKOFF_SEG); a 1a requisicao apos a janela e o teste, 200 zera o contador
- Estado do bloqueio e compartilhado entre TODOS os processos via arquivo
  <tempdir>/kfbot_ip_block.json ({"ate","strikes"}) — bot.py e bot_bg.py usam o mesmo arquivo,
  entao um processo detectar o bloqueio faz os outros pararem sem levar 403 tambem
- Handlers do loop_acoes: BloqueioIPError nao tenta relogar; se o relogin falha durante
  bloqueio, dorme o restante da janela em vez de marcar "cookie_expirado". 403 que NAO e do
  CloudFront segue o caminho HTTPError normal (mensagem "provavel personagem deletado" agora so
  aparece pra 418). scrape_ranking aborta e devolve {} se pegar bloqueio no meio (snapshot
  parcial corromperia o delta da pig list); os 2 chamadores de snapshot inicial checam `if j`
- bot_bg.py tem copia compacta da mesma logica (nao ha modulo compartilhado de proposito:
  launcher.download_update() baixa uma lista FIXA de arquivos, um kf_net.py novo quebraria quem
  atualiza so o bot.py). Se mudar uma, mude a outra
- Ainda NAO tratado: rajada de largada — todos os perfis iniciam ranking+cache ao mesmo tempo
  (e o ranking inicial roda duas vezes: inicializar_background e loop_ranking). Se o bloqueio
  voltar, o proximo passo e escalonar o inicio dos perfis / dedup do ranking inicial

## Aviso de bloqueio de IP no launcher (v2.3.68)
- launcher.py get_bloqueio_ip() le o MESMO arquivo <tempdir>/kfbot_ip_block.json que bot.py e
  bot_bg.py gravam e devolve (segundos_restantes, ocorrencia); get_profiles() poe
  `_ip_bloqueio` / `_ip_bloqueio_n` em cada perfil. Nao depende de status_bot: o aviso some
  sozinho quando a janela expira (sem "parado" velho preso no ultimo_ciclo.json)
- launcher.html: banner ambar global (#ip-banner) + badge no card de cada perfil rodando
  ("IP bloqueado — aguardando ~Xm (sem relogar)"). Atualiza a cada REFRESH (15s)
- Se o bot iniciar e o IP ainda estiver bloqueado: 1a requisicao leva 403 -> grava a janela
  (15min) -> os demais processos leem o arquivo (<=2s) e entram em espera. Se o IP ja foi
  liberado mas o arquivo ainda tem janela futura, apague kfbot_ip_block.json pra destravar
