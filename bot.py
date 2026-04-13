"""
KnightFight Bot v5 — Loop 24h com cache de perfis
==================================================
FLUXO:
  Ao iniciar: coleta cache de perfis (500 perfis, ~15min)
  Às 3h/dia:  recolleta cache de perfis
  A cada 1h:  snapshot do ranking → atualiza pig list
  A cada 2min: verifica CD → ataca pig / imuniza / faz missão
"""

import requests
from bs4 import BeautifulSoup
import json, re, time, logging, os, threading
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional

# ═══════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════
# ── Config padrão (pode ser sobrescrita por argumentos CLI) ──────────────
BASE_URL    = "https://int7.knightfight.moonid.net"

class SessaoExpiradaError(Exception):
    """Cookie vencido — bot perdeu acesso à conta."""
    pass


def _verificar_sessao(r, url=""):
    """Detecta se a resposta indica sessão expirada."""
    txt = r.text
    final_url = r.url.lower()
    # Redirect para login
    if any(k in final_url for k in ["/login", "/signin"]):
        raise SessaoExpiradaError(f"Redirecionado para login: {r.url}")
    # Página muito curta com palavras de login
    if len(txt) < 4000:
        txt_low = txt.lower()
        if any(k in txt_low for k in ["moonid.net/login", "password", "passwort", "forgot password"]):
            raise SessaoExpiradaError(f"Login detectado ({len(txt)} bytes) em {url}")
def fazer_login_moonid(server, username, password):
    """Faz login no jogo via fluxo OAuth moonid.net. Retorna dict com 'cookie' e 'userid'.

    Fluxo correto:
      1. Teaser page do jogo → extrai URL OAuth do botão Login
      2. Visita URL OAuth → redireciona para login moonid.net com next=/api/account/connect/ID/
      3. POST login com esse next → moonid redireciona via OAuth → game server recebe token
      4. Visita /status/ para confirmar sessão e extrair userid
    """
    from urllib.parse import urlparse
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

    # Passo 1: Teaser page → URL OAuth do botão Login (ex: moonid.net/api/account/connect/193)
    r0 = s.get(f"https://{server}.knightfight.moonid.net/", timeout=15)
    connect_url = None
    for a in BeautifulSoup(r0.text, "html.parser").find_all("a", href=True):
        if "/api/account/connect/" in a["href"]:
            connect_url = a["href"]
            break
    if not connect_url:
        raise Exception(f"URL de login não encontrada no servidor '{server}' — servidor existe?")

    # Passo 2: Visita URL OAuth → redireciona para página de login moonid.net
    r1 = s.get(connect_url, timeout=15, allow_redirects=True)
    soup1 = BeautifulSoup(r1.text, "html.parser")

    # Passo 3: Lê CSRF e parâmetro next do formulário de login
    csrf_inp = soup1.find("input", {"name": "csrfmiddlewaretoken"})
    csrf = csrf_inp["value"] if csrf_inp else s.cookies.get("csrftoken", "")
    next_inp = soup1.find("input", {"name": "next"})
    next_val = next_inp["value"] if next_inp else (urlparse(connect_url).path.rstrip("/") + "/")

    # Passo 4: POST login → moonid redireciona via OAuth → game server autentica
    r2 = s.post("https://moonid.net/account/login/", data={
        "username": username, "password": password,
        "csrfmiddlewaretoken": csrf, "next": next_val,
    }, headers={"Referer": r1.url}, timeout=20, allow_redirects=True)
    if "/account/login/" in r2.url:
        raise Exception("Login falhou — usuário ou senha inválidos")

    # Passo 5: Visita /status/ para confirmar sessão no game server e extrair userid
    r3 = s.get(f"https://{server}.knightfight.moonid.net/status/", timeout=20, allow_redirects=True)
    soup3 = BeautifulSoup(r3.text, "html.parser")
    if soup3.find(id="main-teaser-box"):
        raise Exception("OAuth não completou no game server — verifique credenciais e servidor")

    # Coleta todos os cookies da sessão (moonid.net + game server)
    cookies_dict = {c.name: c.value for c in s.cookies}
    if not cookies_dict:
        raise Exception("Nenhum cookie capturado após login")

    # Extrai userid via .your_id na página de status
    userid = ""
    try:
        el = soup3.find(class_="your_id")
        if el:
            m = re.search(r"(\d{6,})", el.get_text())
            if m: userid = m.group(1)
    except Exception:
        pass

    return {"cookie": "; ".join(f"{k}={v}" for k, v in cookies_dict.items()), "userid": userid}

def renovar_cookie_auto(cfg_path="config.json"):
    """Tenta renovar o cookie usando game_user/game_pass do config. Retorna novo cookie ou None."""
    try:
        import json as _j
        with open(cfg_path, encoding="utf-8") as f:
            cfg = _j.load(f)
        game_user = cfg.get("game_user", "")
        game_pass = cfg.get("game_pass", "")
        server    = cfg.get("server", "int7")
        if not game_user or not game_pass:
            return None
        log.info("🔑 Tentando renovar cookie automaticamente...")
        resultado = fazer_login_moonid(server, game_user, game_pass)
        novo_cookie = resultado["cookie"]
        cfg["cookies"] = novo_cookie
        with open(cfg_path, "w", encoding="utf-8") as f:
            _j.dump(cfg, f, indent=2, ensure_ascii=False)
        log.info("✓ Cookie renovado com sucesso!")
        return novo_cookie
    except Exception as e:
        log.error(f"Falha ao renovar cookie: {e}")
        return None

COOKIES_RAW = "COLE_SEUS_COOKIES_AQUI"
MY_USER_ID  = "522001100"
DASHBOARD_PORT = 8765

MY_STATS = {
    "level": 22, "forca": 51, "resistencia": 51,
    "agilidade": 5, "arte_combate": 71, "bloqueio": 71,
    "skill_2maos": 66, "dano_min": 58, "dano_max": 63, "hp": 1540,
}

IS_PREMIUM      = False
GOLD_MIN_PIG    = 50    # gold esperado mínimo para considerar pig
PERDA_XP_MAX    = 0     # máximo de XP a perder (0 = não aceita perder XP)
GOLD_IGNORAR_XP = 500   # pigs acima desse gold ignoram limite de XP

def level_min_xp():
    """Calcula dinamicamente para acompanhar o level atual."""
    return MY_STATS["level"] - 5

COOLDOWN_ATAQUE_SEG   = 300 if IS_PREMIUM else 900
IMUNIDADE_SEG         = 3600
BLOQUEIO_MESMO_PLAYER = 43200
RENOVAR_IMUNIDADE_SEG = 600

HORAS_MISSAO_DIA  = 2 if IS_PREMIUM else 1

MISSAO_ALINHAMENTO   = "bem"  # "bem", "mal", ou "alternado"
TAVERNA_ATIVA        = True   # pode ser sobrescrito pelo config.json
TREINAR_ATRIBUTOS    = False  # treina atributos quando tem gold disponível
BUILD_1MAO           = False  # derivado de BUILD_TIPO automaticamente (não alterar manualmente)
DISTRIBUIR_SKILLS    = False  # distribui pontos de skill ao subir de level
BUILD_TIPO           = "2h"   # "1h" ou "2h" — define como distribuir skills e se treina Agilidade
COMPRAR_EQUIPAMENTO  = False  # compra próximo equip quando acumula gold suficiente
HORARIO_ATIVO        = False  # controle de horário de operação
HORARIO_INICIO       = "08:00"  # hora local de início de operação
HORARIO_PARADA       = "22:00"  # hora local de parada (compra armadura + entra taverna)
SCORE_MIN_PIG        = 70    # score mínimo para pig normal
SCORE_MIN_PIG_BROKE  = 50    # score mínimo para pig quando gold conta <= 100g
SCORE_MIN_IMUNIZACAO = 80    # score mínimo para imunizar
SCORE_MIN_GOLD_ALTO  = 75
GOLD_ALTO_THRESHOLD  = 5000
GOLD_CONTA_BROKE     = 100   # gold na conta considerado "sem gold"

INTERVALO_RAPIDO_SEG = 120
INTERVALO_LENTO_SEG  = 3600
HORA_CACHE_PERFIS    = 3      # 3h da manhã
PAUSA_CACHE_SEG      = 0.5   # pausa entre perfis
RANKING_MAX_PLAYERS  = 500   # quantos jogadores do ranking varrer (100-10000)

RANKING_FILE  = "ranking_snapshots.json"
PIG_LIST_FILE = "pig_list.json"
PERFIS_CACHE  = "perfis_cache.json"
ESTADO_FILE   = "estado.json"
CICLO_FILE    = "ultimo_ciclo.json"
COMBATES_FILE = "combates_srv.json"
MODELO_FILE   = "modelo_combate.json"
LOG_FILE      = "bot.log"

# ═══════════════════════════════════════════
# LOG
# ═══════════════════════════════════════════
# Força UTF-8 no Windows (evita UnicodeEncodeError com emojis no log)
import sys as _sys, io as _io
if hasattr(_sys.stdout, 'buffer') and _sys.stdout.encoding.lower().replace('-','') != 'utf8':
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(_sys.stderr, 'buffer') and _sys.stderr.encoding.lower().replace('-','') != 'utf8':
    _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace')

# Forca UTF-8 no Windows (evita UnicodeEncodeError com emojis no log)
import sys as _sys, io as _io
if hasattr(_sys.stdout, 'buffer') and _sys.stdout.encoding.lower().replace('-','') != 'utf8':
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(_sys.stderr, 'buffer') and _sys.stderr.encoding.lower().replace('-','') != 'utf8':
    _sys.stderr = _io.TextIOWrapper(_sys.stderr.buffer, encoding='utf-8', errors='replace')

def _setup_logging(log_file):
    """
    Configura logging com rotação diária.
    - 1 arquivo por dia: bot.log, bot.log.2026-03-30, etc.
    - Deleta logs com mais de 2 dias automaticamente
    """
    from logging.handlers import TimedRotatingFileHandler
    import glob

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Handler rotativo — meia-noite, mantém 2 dias
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=2,      # mantém hoje + ontem + anteontem (48h)
        encoding="utf-8",
        utc=False,
    )
    file_handler.setFormatter(fmt)
    file_handler.suffix = "%Y-%m-%d"  # bot.log.2026-03-30

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger("KFBot")
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Limpa manualmente logs antigos (>48h) com nome bot.log.YYYY-MM-DD
    try:
        import glob as _g, time as _t
        base = str(log_file)
        for f in _g.glob(base + ".*"):
            if _t.time() - os.path.getmtime(f) > 48 * 3600:
                os.remove(f)
                root.info(f"Log antigo removido: {f}")
    except Exception:
        pass

    return root

log = _setup_logging(LOG_FILE)

# ═══════════════════════════════════════════
# UTILS
# ═══════════════════════════════════════════
def parse_num(txt):
    c = re.sub(r"[^\d]", "", str(txt))
    return int(c) if c else 0

def agora():
    return datetime.now()

def carregar_combates_srv():
    try:
        if Path(COMBATES_FILE).exists():
            return json.loads(Path(COMBATES_FILE).read_text(encoding="utf-8"))
    except: pass
    return []

def salvar_combates_srv(dados):
    Path(COMBATES_FILE).write_text(
        json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

def carregar_modelo():
    """Carrega modelo de aprendizado local ou do GitHub."""
    try:
        if Path(MODELO_FILE).exists():
            return json.loads(Path(MODELO_FILE).read_text(encoding="utf-8"))
    except: pass
    return {}

def registrar_combate_srv(perfil, resultado, gold_ganho, xp_ganho,
                           dano_causado=0, dano_recebido=0, turnos=None):
    """
    Registra combate do servidor original para aprendizado.
    Salva atributos de ambos os lados + resultado para análise futura.
    """
    eu = MY_STATS.copy()
    combates = carregar_combates_srv()
    registro = {
        "ts": agora().isoformat(),
        "resultado": resultado,
        "gold": gold_ganho,
        "xp": xp_ganho,
        "dano_causado": round(dano_causado, 1),
        "dano_recebido": round(dano_recebido, 1),
        # Meus stats no momento do combate
        "eu_lv":  eu.get("level", 0),
        "eu_ac":  eu.get("arte_combate", 0),
        "eu_blq": eu.get("bloqueio", 0),
        "eu_frc": eu.get("forca", 0),
        "eu_agi": eu.get("agilidade", 0),
        "eu_res": eu.get("resistencia", 0),
        "eu_s1":  eu.get("sk_1mao", 0),
        "eu_s2":  eu.get("sk_2maos", 0),
        "eu_arm": eu.get("sk_armadura", 0),
        # Stats do adversário
        "adv_id":   perfil.get("user_id", ""),
        "adv_nome": perfil.get("nome", ""),
        "adv_lv":   perfil.get("level", 0),
        "adv_ac":   perfil.get("arte_combate", 0),
        "adv_blq":  perfil.get("bloqueio", 0),
        "adv_frc":  perfil.get("forca", 0),
        "adv_agi":  perfil.get("agilidade", 0),
        "adv_res":  perfil.get("resistencia", 0),
        "adv_arm":  perfil.get("sk_armadura", 0),
        "adv_s1":   perfil.get("sk_1mao", 0),
        "adv_s2":   perfil.get("sk_2maos", 0),
        "score_previsto": perfil.get("_score_cache", 0),
        "score_sim":      perfil.get("_score_sim", perfil.get("_score_cache", 0)),
        # Dano/defesa calculados pelo simulador para calibração
        "sim_dano_eu":    round(perfil.get("_sim_dano_eu", 0), 2),
        "sim_dano_adv":   round(perfil.get("_sim_dano_adv", 0), 2),
        "sim_def_eu":     round(perfil.get("_sim_def_eu", 0), 2),
        "sim_def_adv":    round(perfil.get("_sim_def_adv", 0), 2),
        # Dados reais do combate para calibrar simulador
        "hits_eu":          (turnos or {}).get("hits_eu", 0),
        "misses_eu":        (turnos or {}).get("misses_eu", 0),
        "hits_adv":         (turnos or {}).get("hits_adv", 0),
        "misses_adv":       (turnos or {}).get("misses_adv", 0),
        "taxa_acerto_eu":   (turnos or {}).get("taxa_acerto_eu", 0),
        "taxa_acerto_adv":  (turnos or {}).get("taxa_acerto_adv", 0),
        "rounds_real":      (turnos or {}).get("rounds", 0),
        "crits_eu":         (turnos or {}).get("crits_eu", 0),
        "crits_adv":        (turnos or {}).get("crits_adv", 0),
        "dano_bloqueado_eu": (turnos or {}).get("dano_bloqueado_eu", 0),
    }
    combates.append(registro)
    # Mantém apenas os últimos 2000 combates
    if len(combates) > 2000:
        combates = combates[-2000:]
    salvar_combates_srv(combates)

    # Recalcula modelo a cada 20 combates novos
    if len(combates) % 20 == 0:
        gerar_modelo(combates)

    return registro

def gerar_modelo(combates=None):
    """
    Gera modelo_combate.json com insights estatísticos dos combates.
    Pode ser subido ao GitHub para compartilhar com outros usuários.
    """
    if combates is None:
        combates = carregar_combates_srv()
    if len(combates) < 10:
        return {}

    total = len(combates)
    vitorias = sum(1 for c in combates if c["resultado"] == "vitoria")

    # WR por faixa de delta_AC (meu AC - bloqueio dele)
    wr_delta_ac = {}
    for c in combates:
        eu_ac  = c.get("eu_ac", 0)
        adv_blq = c.get("adv_blq", 0)
        if eu_ac > 0 and adv_blq > 0:
            taxa = round(eu_ac / (eu_ac + adv_blq) * 10) / 10  # arredonda para 0.1
            faixa = f"{taxa:.1f}"
            if faixa not in wr_delta_ac:
                wr_delta_ac[faixa] = {"t": 0, "v": 0}
            wr_delta_ac[faixa]["t"] += 1
            if c["resultado"] == "vitoria":
                wr_delta_ac[faixa]["v"] += 1

    wr_delta_ac_calc = {
        k: round(v["v"]/v["t"]*100, 1)
        for k, v in wr_delta_ac.items() if v["t"] >= 3
    }

    # WR por faixa de delta_level
    wr_delta_lv = {}
    for c in combates:
        delta = c.get("adv_lv", 0) - c.get("eu_lv", 0)
        faixa = str(max(-5, min(10, delta)))  # clamp -5 a +10
        if faixa not in wr_delta_lv:
            wr_delta_lv[faixa] = {"t": 0, "v": 0}
        wr_delta_lv[faixa]["t"] += 1
        if c["resultado"] == "vitoria":
            wr_delta_lv[faixa]["v"] += 1

    wr_delta_lv_calc = {
        k: round(v["v"]/v["t"]*100, 1)
        for k, v in wr_delta_lv.items() if v["t"] >= 3
    }

    # Score previsto vs resultado real (calibração)
    score_calibracao = {}
    for c in combates:
        sp = c.get("score_previsto", 0)
        faixa = str(int(sp // 10) * 10)  # faixas de 10 em 10
        if faixa not in score_calibracao:
            score_calibracao[faixa] = {"t": 0, "v": 0}
        score_calibracao[faixa]["t"] += 1
        if c["resultado"] == "vitoria":
            score_calibracao[faixa]["v"] += 1

    calibracao = {
        k: round(v["v"]/v["t"]*100, 1)
        for k, v in score_calibracao.items() if v["t"] >= 3
    }

    modelo = {
        "versao": agora().strftime("%Y%m%d_%H%M"),
        "total_combates": total,
        "win_rate_global": round(vitorias/total*100, 1),
        "wr_por_hit_rate": wr_delta_ac_calc,
        "wr_por_delta_level": wr_delta_lv_calc,
        "calibracao_score": calibracao,
        "gold_total": sum(c.get("gold", 0) for c in combates),
        "xp_total": sum(c.get("xp", 0) for c in combates),
    }

    Path(MODELO_FILE).write_text(
        json.dumps(modelo, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"Modelo atualizado: {total} combates | WR {modelo['win_rate_global']}%")
    return modelo

def seg_desde(iso):
    if not iso: return float("inf")
    return (agora() - datetime.fromisoformat(iso)).total_seconds()

def seg_ate(iso):
    if not iso: return 0
    return max(0, (datetime.fromisoformat(iso) - agora()).total_seconds())

def fmt_t(seg):
    seg = int(seg)
    if seg <= 0: return "agora"
    h, r = divmod(seg, 3600)
    m, s = divmod(r, 60)
    if h: return f"{h}h{m:02d}m"
    if m: return f"{m}m{s:02d}s"
    return f"{s}s"

# ═══════════════════════════════════════════
# ESTADO
# ═══════════════════════════════════════════
_estado_lock = __import__("threading").Lock()

def carregar_estado():
    with _estado_lock:
        if os.path.exists(ESTADO_FILE):
            with open(ESTADO_FILE, encoding="utf-8") as f:
                return json.load(f)
    return {
        "ultimo_ataque": None,
        "imunidade_ate": None,
        "minutos_missao_hoje": 0,
        "missoes_hoje": 0,
        "dia_atual": agora().strftime("%Y-%m-%d"),
        "historico_ataques": {},
        "gold_atual": 0,
    }

def salvar_estado(e):
    hoje = agora().strftime("%Y-%m-%d")
    if e.get("dia_atual") != hoje:
        e["missoes_hoje"] = 0
        e["minutos_missao_hoje"] = 0
        e["dia_atual"] = hoje
        log.info("Novo dia — contadores resetados")
    # Escrita atômica: grava em arquivo temp e renomeia — evita corrupção por race condition entre threads
    tmp = ESTADO_FILE + ".tmp"
    with _estado_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(e, f, indent=2, ensure_ascii=False)
        os.replace(tmp, ESTADO_FILE)

def registrar_ataque(estado, user_id, resultado="desconhecido", gold_ganho=0, xp_ganho=0):
    estado["ultimo_ataque"] = agora().isoformat()
    estado["imunidade_ate"] = (agora() + timedelta(seconds=IMUNIDADE_SEG)).isoformat()
    if "historico_ataques" not in estado:
        estado["historico_ataques"] = {}
    estado["historico_ataques"][user_id] = agora().isoformat()

    # Estatísticas do dia
    hoje = agora().strftime("%Y-%m-%d")
    if estado.get("stats_dia_data") != hoje:
        estado["stats_dia_data"] = hoje
        estado["stats_dia"] = {"ataques": 0, "vitorias": 0, "derrotas": 0,
                                "gold_ganho": 0, "gold_perdido": 0}
    sd = estado.setdefault("stats_dia", {"ataques": 0, "vitorias": 0, "derrotas": 0,
                                          "gold_ganho": 0, "gold_perdido": 0})
    sd["ataques"] += 1
    if resultado == "vitoria":
        sd["vitorias"] += 1
        sd["gold_ganho"] += gold_ganho
    elif resultado == "derrota":
        sd["derrotas"] += 1
        # gold_ganho contém o que o oponente ganhou (= o que perdemos)
        if gold_ganho > 0:
            sd["gold_perdido"] = sd.get("gold_perdido", 0) + gold_ganho

    salvar_estado(estado)
    log.info(f"Imunidade renovada até {agora() + timedelta(seconds=IMUNIDADE_SEG):%H:%M:%S}")

def pode_atacar_player(estado, user_id):
    ultimo = estado.get("historico_ataques", {}).get(user_id)
    if not ultimo: return True, ""
    s = seg_desde(ultimo)
    if s < BLOQUEIO_MESMO_PLAYER:
        return False, f"bloqueio 12h: faltam {fmt_t(BLOQUEIO_MESMO_PLAYER - s)}"
    return True, ""

def imunidade_restante(estado):
    return seg_ate(estado.get("imunidade_ate"))

def cooldown_restante(estado):
    ultimo = estado.get("ultimo_ataque")
    if not ultimo: return 0
    return max(0, COOLDOWN_ATAQUE_SEG - seg_desde(ultimo))

def atualizar_ciclo_file(chave, valor):
    """Atualiza uma chave no ciclo_file sem sobrescrever o resto."""
    ciclo = {}
    if os.path.exists(CICLO_FILE):
        try:
            with open(CICLO_FILE, encoding="utf-8") as f:
                ciclo = json.load(f)
        except:
            pass
    ciclo[chave] = valor
    ciclo["timestamp"] = agora().isoformat()
    with open(CICLO_FILE, "w", encoding="utf-8") as f:
        json.dump(ciclo, f, indent=2, ensure_ascii=False)

# ═══════════════════════════════════════════
# CLIENTE HTTP
# ═══════════════════════════════════════════
class KFClient:
    def __init__(self, cookies_raw):
        self.session = requests.Session()
        for part in cookies_raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                self.session.cookies.set(k.strip(), v.strip())
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE_URL + "/",
        })

    def get(self, path, fragment=True):
        url = BASE_URL + path
        if fragment:
            url += ("&" if "?" in path else "?") + "fragment=1"
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        _verificar_sessao(r, url)
        return BeautifulSoup(r.text, "html.parser")

    def get_url(self, url):
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        _verificar_sessao(r, url)
        return BeautifulSoup(r.text, "html.parser")

    def post(self, path, data, fragment=True):
        url = BASE_URL + path
        if fragment:
            url += ("&" if "?" in path else "?") + "fragment=1"
        r = self.session.post(url, data=data, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")

# ═══════════════════════════════════════════
# CACHE DE PERFIS — atualizado 1x/dia
# ═══════════════════════════════════════════
def carregar_perfis_cache():
    if os.path.exists(PERFIS_CACHE):
        try:
            with open(PERFIS_CACHE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            log.warning("Cache de perfis corrompido — recriando.")
    return {"atualizado_em": None, "perfis": {}}

def salvar_perfis_cache(cache):
    with open(PERFIS_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

def cache_precisa_atualizar():
    """True se nunca foi gerado ou se foi há mais de 20h."""
    cache = carregar_perfis_cache()
    if not cache.get("atualizado_em"):
        return True
    return seg_desde(cache["atualizado_em"]) / 3600 >= 20

def coletar_perfis_cache(client):
    """
    Visita os 500 perfis do ranking e salva atributos completos.
    Leva ~15min com 2s de pausa. Roda ao iniciar e às 3h/dia.

    Por que salvar cache:
    - Avaliar alvo requer arte_combate, bloqueio, resistencia do perfil
    - Visitar perfil em tempo real (durante tentativa de ataque) é lento
    - Com cache, a decisão de "vou ganhar?" é instantânea
    - Só precisamos verificar disponibilidade (botão Attack) em tempo real
    """
    snaps = carregar_snapshots()
    if not snaps:
        log.warning("Cache de perfis: sem ranking disponível ainda")
        return

    jogadores = list(snaps[-1]["jogadores"].values())
    total = len(jogadores)
    log.info(f"Coletando cache de perfis: {total} jogadores (~{total*PAUSA_CACHE_SEG/60:.0f}min)...")

    cache = carregar_perfis_cache()
    perfis = cache.get("perfis", {})
    atualizados = 0
    erros = 0

    for i, j in enumerate(jogadores):
        uid = j["user_id"]
        if uid == MY_USER_ID:
            continue

        try:
            soup = client.get_url(f"{BASE_URL}/player/{uid}/")
            perfil = parsear_perfil(soup, uid)
            p = {
                **perfil,
                "nome": j["nome"],
                "level": perfil["level"] or j["level"],
                "win_rate_ranking": round(j["vitorias"] / j["combates"] * 100, 1) if j.get("combates", 0) > 0 else 50,
                "coletado_em": agora().isoformat(),
            }
            # Calcula score com simulador já na coleta
            try:
                av = avaliar_alvo(p)
                p["_score"] = av["score"]
                p["_rec"]   = av["recomendacao"]
            except Exception:
                p["_score"] = 0
                p["_rec"]   = "EVITAR"
            perfis[uid] = p
            atualizados += 1
            if atualizados % 50 == 0:
                log.info(f"  Cache: {atualizados}/{total} perfis coletados...")
                # Salva parcialmente a cada 50 para não perder tudo se interromper
                cache["perfis"] = perfis
                cache["atualizado_em"] = agora().isoformat()
                salvar_perfis_cache(cache)
        except Exception as e:
            erros += 1
            log.debug(f"  Erro perfil {uid}: {e}")

        time.sleep(PAUSA_CACHE_SEG)

    cache["perfis"] = perfis
    cache["atualizado_em"] = agora().isoformat()
    salvar_perfis_cache(cache)
    log.info(f"Cache de perfis concluído: {atualizados} coletados, {erros} erros")

def candidatos_imunizacao_do_cache(estado):
    """
    Retorna lista de candidatos para imunização ordenados por:
    1. Score de vitória (maior = mais seguro)
    2. Level próximo ao meu (prefere ±5)

    Usa cache de perfis — sem requisições HTTP.
    Filtra: level >= level_min_xp(), sem bloqueio 12h, sem ser eu mesmo.
    """
    cache = carregar_perfis_cache()
    if not cache.get("perfis"):
        return []

    meu_lv = MY_STATS["level"]
    candidatos = []

    for uid, p in cache["perfis"].items():
        if uid == MY_USER_ID:
            continue
        if p.get("level", 0) < level_min_xp():
            continue
        pode, _ = pode_atacar_player(estado, uid)
        if not pode:
            continue

        av = avaliar_alvo(p)
        delta_lv = abs(p.get("level", 0) - meu_lv)
        candidatos.append({
            "user_id": uid,
            "nome": p.get("nome", "?"),
            "level": p.get("level", 0),
            "score": av["score"],
            "recomendacao": av["recomendacao"],
            "delta_lv": delta_lv,
        })

    # Ordena: score alto primeiro, depois level mais próximo
    candidatos.sort(key=lambda x: (-x["score"], x["delta_lv"]))
    return candidatos

# ═══════════════════════════════════════════
# PERFIL + AVALIAÇÃO
# ═══════════════════════════════════════════
def _clan_id_de_perfil(soup):
    """Extrai clan_id do perfil. Retorna None se sem clan."""
    for tag in soup.find_all("a", href=True):
        m = re.search(r"/clan/(\d+)/", tag["href"])
        if m:
            return int(m.group(1))
    return None


def parsear_perfil(soup, user_id):
    def extrair(nomes):
        """
        Suporta dois formatos de tooltip:
        - Perfil alheio: "Força: (56)"  → captura com parênteses
        - Status próprio: "Strength: 51 + 2" → captura primeiro número após ":"
        """
        for tag in soup.find_all(attrs={"data-tooltip": True}):
            tip = tag["data-tooltip"]
            for n in nomes:
                if n.lower() in tip.lower():
                    m_par = re.search(r'\((\d+)\)', tip)
                    if m_par: return int(m_par.group(1))
                    m_col = re.search(r':\s*(\d+)', tip)
                    if m_col: return int(m_col.group(1))
        return 0

    title = soup.find(id="character-title")
    nome = title.get_text(strip=True) if title else "?"

    level = 0
    for tag in soup.find_all(attrs={"data-tooltip": True}):
        if "Level:" in tag.get("data-tooltip", ""):
            m = re.search(r"Level:\s*(\d+)", tag["data-tooltip"])
            if m: level = int(m.group(1))

    hp = 0
    for tag in soup.find_all(attrs={"data-tooltip": True}):
        tip = tag["data-tooltip"]
        if "Health points:" in tip:
            m = re.search(r"of\s*([\d.]+)", tip)
            if m: hp = parse_num(m.group(1))

    disponivel = bool(soup.find("a", href=lambda h: h and "raubzug/gegner" in h))

    return {
        "nome": nome, "user_id": user_id, "level": level,
        "forca":        extrair(["força", "strength"]),
        "resistencia":  extrair(["resistência", "stamina"]),
        "agilidade":    extrair(["agilidade", "dexterity"]),
        "arte_combate": extrair(["arte de combate", "fighting ability"]),
        "bloqueio":     extrair(["bloqueio", "parry"]),
        "sk_armadura":  extrair(["armour skill:", "skill de armadura:"]),
        "sk_1mao":      extrair(["one-handed attack:", "skill uma mão:"]),
        "sk_2maos":     extrair(["two-handed attack:", "skill duas mãos:"]),
        "hp": hp, "disponivel": disponivel,
        "clan_id": _clan_id_de_perfil(soup),
    }

def avaliar_alvo(perfil, eu=None):
    """
    Score 0-100 para chance de vitória.

    Fórmula melhorada com base em análise de combates reais:
    - AC e bloqueio (hit rate)
    - Level delta (equipamento implícito)
    - Força do alvo (dano bruto)
    - Resistência (rounds de combate)
    - Atributos de equipamento do alvo (sk_armadura como proxy de defesa)
    """
    if eu is None:
        eu = MY_STATS

    minha_ac  = eu.get("arte_combate", 0)
    meu_blq   = eu.get("bloqueio", 0)
    minha_frc = eu.get("forca", 0)
    meu_lv    = eu.get("level", 0)
    minha_res = eu.get("resistencia", 0)

    problemas = []
    vantagens = []
    score = 50

    blq    = perfil.get("bloqueio", 0)
    ac_d   = perfil.get("arte_combate", 0)
    res_d  = perfil.get("resistencia", 0)
    frc_d  = perfil.get("forca", 0)
    lv_d   = perfil.get("level", 0)
    arm_d  = perfil.get("sk_armadura", 0)
    agil_d = perfil.get("agilidade", 0)   # agilidade real (pode ser negativa com arma pesada)
    sk1_d  = perfil.get("sk_1mao", 0)
    sk2_d  = perfil.get("sk_2maos", 0)
    sk_d   = max(sk1_d, sk2_d)  # skill principal de ataque do alvo
    meu_sk1 = eu.get("sk_1mao", 0)
    meu_sk2 = eu.get("sk_2maos", 0)
    meu_sk  = max(meu_sk1, meu_sk2)
    minha_agil = eu.get("agilidade", 0)

    # Detecta build do adversário
    # Build 2h: sk_2maos alto, sk_armadura baixo → dano alto, defesa baixa
    # Build 1h: sk_1mao alto, sk_armadura alto → dano menor, defesa maior (agilidade importa)
    usa_2h   = sk2_d > sk1_d and sk2_d > 0
    usa_arm  = arm_d > 20  # tem investimento em armadura/escudo

    # ── 1. Level delta — mais importante que tudo ─────────────────────────────
    delta_lv = lv_d - meu_lv
    if delta_lv >= 10:
        problemas.append(f"Level {lv_d} vs {meu_lv} (+{delta_lv}) — equipamento muito superior")
        score -= 35   # quase sempre perde
    elif delta_lv >= 7:
        problemas.append(f"Level {lv_d} vs {meu_lv} (+{delta_lv}) — equipamento superior")
        score -= 20
    elif delta_lv >= 4:
        problemas.append(f"Level {lv_d} vs {meu_lv} (+{delta_lv}) — pequena desvantagem")
        score -= 10
    elif delta_lv <= -4:
        vantagens.append(f"Level {lv_d} vs {meu_lv} ({delta_lv}) — equipamento inferior ✓")
        score += 8

    # ── 2. Minha taxa de acerto (AC vs bloqueio do alvo) ──────────────────────
    taxa = minha_ac / (minha_ac + blq) if blq > 0 else 1.0
    if blq > 0:
        if taxa < 0.35:
            problemas.append(f"Hit rate {taxa*100:.0f}% — bloqueio {blq} muito alto")
            score -= 40
        elif taxa < 0.42:
            problemas.append(f"Hit rate {taxa*100:.0f}% — difícil acertar")
            score -= 28
        elif taxa < 0.48:
            problemas.append(f"Hit rate {taxa*100:.0f}% — abaixo do ideal")
            score -= 15
        elif taxa < 0.52:
            problemas.append(f"Hit rate {taxa*100:.0f}% — levemente abaixo")
            score -= 5
        else:
            vantagens.append(f"Hit rate {taxa*100:.0f}% ✓")
            score += 15

    # ── 3. Taxa de acerto dele (AC dele vs meu bloqueio) ─────────────────────
    taxa_d = ac_d / (ac_d + meu_blq) if ac_d > 0 and meu_blq > 0 else 0.0
    if ac_d > 0 and meu_blq > 0:
        if taxa_d > 0.70:
            problemas.append(f"AC dele {ac_d} vs meu bloqueio {meu_blq} → {taxa_d*100:.0f}% — ele acerta muito")
            score -= 20
        elif taxa_d > 0.58:
            problemas.append(f"AC dele {ac_d} → {taxa_d*100:.0f}% de acerto")
            score -= 8
        elif taxa_d < 0.45:
            vantagens.append(f"Meu bloqueio segura {(1-taxa_d)*100:.0f}% ✓")
            score += 12

    # ── 3b. Penalidade extra: build especializada (AC e Blq ambos superiores) ─
    # Ex: azrael Lv18 com AC87/Blq87 — level baixo mas build focada em combate
    if ac_d > minha_ac and blq > meu_blq:
        vantagem_dupla = ((ac_d - minha_ac) + (blq - meu_blq)) / 2
        if vantagem_dupla > 15:
            problemas.append(f"Build especializada: AC {ac_d} > {minha_ac} E Blq {blq} > {meu_blq} — desvantagem dupla")
            score -= 20
        elif vantagem_dupla > 8:
            problemas.append(f"AC e bloqueio superiores — build focada em combate")
            score -= 12

    # ── 4. Força do alvo (proxy de dano bruto) ────────────────────────────────
    if frc_d > minha_frc * 2.0:
        problemas.append(f"Força {frc_d} >> {minha_frc} — dano muito alto")
        score -= 20
    elif frc_d > minha_frc * 1.5:
        problemas.append(f"Força {frc_d} > {minha_frc} — dano alto")
        score -= 10
    elif frc_d > 0 and frc_d < minha_frc * 0.7:
        vantagens.append(f"Força {frc_d} << {minha_frc} ✓")
        score += 8

    # ── 5. Armadura + Agilidade (defesa real) ────────────────────────────────
    # Quem usa armadura/escudo tem bônus de defesa pela agilidade
    # Quem usa 2h sem armadura a agilidade não importa
    if usa_arm:
        # Defesa real = armadura + bônus agilidade
        # Agilidade positiva = mais defesa, negativa = menos defesa
        defesa_efetiva = arm_d + max(0, agil_d // 5)  # estimativa do bônus
        if defesa_efetiva > 60:
            problemas.append(f"Defesa alta: arm={arm_d} agil={agil_d} → defesa efetiva ~{defesa_efetiva}")
            score -= 18
        elif defesa_efetiva > 35:
            problemas.append(f"Boa defesa: arm={arm_d} agil={agil_d}")
            score -= 10
        elif arm_d > 0 and agil_d < -5:
            # Tem armadura mas agilidade negativa (arma pesada) → defesa comprometida
            vantagens.append(f"Armadura {arm_d} com agil negativa {agil_d} — defesa reduzida ✓")
            score += 5
    else:
        # Build 2h sem armadura → sem bônus de defesa, mas dano alto
        if usa_2h:
            vantagens.append(f"Build 2h sem armadura — defesa mínima ✓")
            score += 8  # mais fácil acertar e causar dano

    # ── 5b. Build 2h: penalidade pelo dano alto ──────────────────────────────
    if usa_2h and sk2_d > 0:
        # Arma 2h tem dano base muito maior → mais perigoso
        if sk2_d > meu_sk * 1.3:
            problemas.append(f"Build 2h com skill {sk2_d} > minha {meu_sk} — dano alto")
            score -= 12
        elif sk2_d > meu_sk:
            score -= 5

    # ── Skill de ataque do alvo (1h ou 2h) ───────────────────────────────────
    if sk_d > 0 and meu_sk > 0:
        diff_sk = sk_d - meu_sk
        if diff_sk > 30:
            problemas.append(f"Skill ataque {sk_d} vs minha {meu_sk}")
            score -= 15
        elif diff_sk > 15:
            score -= 8
        elif diff_sk < -20:
            vantagens.append(f"Minha skill {meu_sk} supera {sk_d} ✓")
            score += 10

    # ── 6. Resistência (rounds extras = risco de virada) ─────────────────────
    if res_d > minha_res * 1.8:
        problemas.append(f"Resistência {res_d} >> minha {minha_res} — rounds favorecem ele")
        score -= 10
    elif res_d > 0 and res_d < minha_res * 0.6:
        vantagens.append(f"Resistência {res_d} baixa ✓")
        score += 8

    # ── 7. Ajuste por modelo aprendido ───────────────────────────────────────
    modelo = carregar_modelo()
    if modelo and modelo.get("total_combates", 0) >= 20:
        # Ajuste pelo hit rate real aprendido
        if blq > 0 and minha_ac > 0:
            taxa_key = f"{taxa:.1f}"
            wr_hr = modelo.get("wr_por_hit_rate", {}).get(taxa_key)
            if wr_hr is not None:
                # Blend: 70% fórmula, 30% aprendizado real
                score_aprendido = wr_hr
                score = round(score * 0.7 + score_aprendido * 0.3)
                vantagens.append(f"Modelo: hit rate {taxa*100:.0f}% → WR real {wr_hr}%")                     if wr_hr >= 60 else problemas.append(f"Modelo: hit rate real {wr_hr}%")

        # Ajuste pelo delta level real aprendido
        delta_key = str(max(-5, min(10, delta_lv)))
        wr_lv = modelo.get("wr_por_delta_level", {}).get(delta_key)
        if wr_lv is not None:
            score = round(score * 0.8 + wr_lv * 0.2)

    score = max(0, min(100, score))

    # ── Simulação de combate com tabelas reais ────────────────────────────────
    try:
        from combat_sim import simular_combate
        sim = simular_combate(eu, perfil)
        sim_score = sim["score"]
        # 100% simulação — mais preciso que heurística
        score = sim_score
        perfil["_score_sim"]   = sim_score  # salva para registro
        perfil["_score_cache"] = sim_score  # sync cache com simulador
        # Salva dano/def calculados para análise de calibração
        perfil["_sim_dano_eu"]  = sim.get("dano_eu", 0)
        perfil["_sim_dano_adv"] = sim.get("dano_adv", 0)
        perfil["_sim_def_eu"]   = sim.get("def_eu", 0)
        perfil["_sim_def_adv"]  = sim.get("def_adv", 0)
        score = max(0, min(100, score))
        vantagens.append(f"Sim: dano_eu={sim['total_eu']} vs dano_adv={sim['total_adv']} | taxa={sim['taxa_eu']}%")
    except Exception as e:
        pass  # Se falhar usa só heurística

    rec = "ATACAR" if score >= 60 else ("CUIDADO" if score >= 40 else "EVITAR")
    return {"recomendacao": rec, "score": score,
            "vantagens": vantagens, "problemas": problemas}

def parsear_turnos_combate(turns_json, eu_fui_atacante=True):
    """
    Extrai estatísticas detalhadas dos turnos do displayFightReport.
    turns_json: lista de dicts com p, a, d, b, c
    Retorna dict com hits/misses/dano de cada lado.
    """
    stats = {
        "hits_eu": 0, "misses_eu": 0,
        "hits_adv": 0, "misses_adv": 0,
        "dano_eu": 0.0, "dano_adv": 0.0,
        "dano_bloqueado_eu": 0.0, "dano_bloqueado_adv": 0.0,
        "crits_eu": 0, "crits_adv": 0,
        "rounds": 0,
    }
    if not turns_json:
        return stats

    # p="a" = attacker agiu, p="d" = defender agiu
    # Se eu fui atacante: "a" = eu, "d" = adv
    # Se fui defensor:    "d" = eu, "a" = adv
    meu_lado  = "a" if eu_fui_atacante else "d"
    adv_lado  = "d" if eu_fui_atacante else "a"

    for t in turns_json:
        p = t.get("p", "")
        acao = t.get("a", "")
        dano = float(t.get("d", 0) or 0)
        bloq = float(t.get("b", 0) or 0)
        crit = bool(t.get("c", False))

        if p == meu_lado:
            stats["rounds"] += 1
            if acao == "h":
                stats["hits_eu"] += 1
                stats["dano_eu"] += dano
                stats["dano_bloqueado_adv"] += bloq
                if crit: stats["crits_eu"] += 1
            else:
                stats["misses_eu"] += 1
        elif p == adv_lado:
            if acao == "h":
                stats["hits_adv"] += 1
                stats["dano_adv"] += dano
                stats["dano_bloqueado_eu"] += bloq
                if crit: stats["crits_adv"] += 1
            else:
                stats["misses_adv"] += 1

    # Taxa de acerto real
    total_eu  = stats["hits_eu"]  + stats["misses_eu"]
    total_adv = stats["hits_adv"] + stats["misses_adv"]
    stats["taxa_acerto_eu"]  = round(stats["hits_eu"]  / total_eu  * 100, 1) if total_eu  > 0 else 0
    stats["taxa_acerto_adv"] = round(stats["hits_adv"] / total_adv * 100, 1) if total_adv > 0 else 0
    stats["dano_eu"]  = round(stats["dano_eu"],  1)
    stats["dano_adv"] = round(stats["dano_adv"], 1)
    stats["dano_bloqueado_eu"]  = round(stats["dano_bloqueado_eu"],  1)
    stats["dano_bloqueado_adv"] = round(stats["dano_bloqueado_adv"], 1)
    return stats


def parsear_resultado_combate(soup, eu_fui_atacante=True):
    """
    Extrai resultado do relatório de combate.

    O resultado está em dois lugares:
    1. JSON do displayFightReport: "winner": "attacker" ou "defender"
       - Se eu ataquei (eu_fui_atacante=True): winner=attacker → vitória
       - Se fui atacado (eu_fui_atacante=False): winner=defender → vitória
    2. HTML: gold e XP ganhos ficam como "238 [img gold_coin]"
    Também extrai turnos para calibração do simulador.
    """
    resultado = "desconhecido"
    gold_ganho = 0
    xp_ganho = 0
    turnos_stats = {}

    # 1. Extrai winner e turns do JSON do displayFightReport
    for script in soup.find_all("script"):
        txt = script.string or ""
        if "displayFightReport" not in txt:
            continue
        m = re.search(r'"winner"\s*:\s*"(\w+)"', txt)
        if m:
            winner = m.group(1)
            if eu_fui_atacante:
                resultado = "vitoria" if winner == "attacker" else "derrota"
            else:
                resultado = "vitoria" if winner == "defender" else "derrota"
        # Extrai turnos
        try:
            m_turns = re.search(r'"turns"\s*:\s*(\[.*?\])', txt, re.DOTALL)
            if m_turns:
                import json as _json
                turns = _json.loads(m_turns.group(1))
                turnos_stats = parsear_turnos_combate(turns, eu_fui_atacante)
        except Exception:
            pass
        break

    # 2. Extrai gold e XP do HTML
    html_txt = str(soup)
    m_gold = re.search(r"(\d+)\s*<img[^>]*gold_coin[^>]*>", html_txt)
    if m_gold:
        gold_ganho = int(m_gold.group(1))

    m_xp = re.findall(r"(-?\d+)\s*<img[^>]*exp_scroll[^>]*>", html_txt)
    for v in m_xp:
        val = int(v)
        if val != 0:
            xp_ganho = val
            break

    # Se o HTML não mostrou XP negativo mas foi derrota, nega o valor
    if resultado == "derrota" and xp_ganho > 0:
        xp_ganho = -xp_ganho

    return resultado, gold_ganho, xp_ganho, turnos_stats


def parsear_confirmacao_ataque(soup):
    """
    Parseia a página de confirmação de ataque:
    GET /raubzug/gegner/?searchuserid=ID
    
    Retorna:
      csrf: token para o POST
      gegnerid: ID do alvo (pode ser diferente do searchuserid em buscas por nome)
      attrs: atributos do alvo (força, resistência, etc.) — mais confiáveis que o cache
      disponivel: True se o botão Attack está presente
    
    Atributos vêm no formato: <img ...>(56) — número entre parênteses após a barra
    """
    csrf = ""
    gegnerid = ""
    attrs = {}
    disponivel = False

    # Extrai form de ataque
    for form in soup.find_all("form"):
        inp = form.find("input", {"name": "sac", "value": "attack"})
        if inp:
            disponivel = True
            csrf_inp = form.find("input", {"name": "csrftoken"})
            if csrf_inp: csrf = csrf_inp.get("value", "")
            geg_inp = form.find("input", {"name": "gegnerid"})
            if geg_inp: gegnerid = geg_inp.get("value", "")
            break

    # Extrai atributos das barras: padrão "(56)" no texto
    attr_map = {
        "Força": "forca", "Resistência": "resistencia", "Agilidade": "agilidade",
        "Arte de combate": "arte_combate", "Bloqueio": "bloqueio",
    }
    rows = soup.find_all("td", class_="attack-now-attr")
    for td in rows:
        label = td.get_text(strip=True).rstrip(":")
        val_td = td.find_next_sibling("td")
        if val_td:
            m = re.search(r"\((\d+)\)", val_td.get_text())
            if m and label in attr_map:
                attrs[attr_map[label]] = int(m.group(1))

    return csrf, gegnerid, attrs, disponivel


def verificar_alvo_antes_de_atacar(client, user_id, score_min, meu_clan_id=None):
    """
    Antes de atacar: visita perfil do alvo para:
    1. Confirmar que ainda está disponível
    2. Checar se é da mesma guild
    3. Recalcular score com stats atuais
    Retorna (ok, score_atual, motivo)
    """
    try:
        soup = client.get_url(f"{BASE_URL}/player/{user_id}/")
        perfil = parsear_perfil(soup, user_id)

        # Check guild
        if meu_clan_id and perfil.get("clan_id") == meu_clan_id:
            return False, 0, "mesma_guild"

        if not perfil.get("disponivel"):
            return False, 0, "indisponivel"

        # Recalcula score com stats frescos
        av = avaliar_alvo(perfil)
        score_atual = av["score"]

        if score_atual < score_min:
            return False, score_atual, f"score_baixo({score_atual}<{score_min})"

        # Atualiza cache com stats frescos
        cache = carregar_perfis_cache()
        if user_id in cache.get("perfis", {}):
            cache["perfis"][user_id].update(perfil)
            cache["perfis"][user_id]["_score"] = score_atual
            salvar_perfis_cache(cache)

        return True, score_atual, "ok"
    except Exception as e:
        log.debug(f"verificar_alvo {user_id}: {e}")
        return True, score_min, "erro_verificacao"  # em caso de erro, tenta atacar mesmo assim


def executar_ataque(client, user_id, dry_run=False):
    """
    Fluxo correto de ataque (2 passos):
    1. GET /raubzug/gegner/?searchuserid=ID → página de confirmação
       - Verifica disponibilidade real
       - Extrai CSRF e gegnerid para o POST
       - Parseia atributos atuais do alvo
    2. POST / com ac=raubzug, sac=attack, gegnerid, csrftoken → resultado
       - Parseia vitória/derrota via displayFightReport
       - Extrai gold e XP ganhos
    """
    if dry_run:
        log.info(f"[DRY] Ataque simulado em {user_id}")
        return {"status": "dry_run"}

    # Passo 1: página de confirmação
    url = f"{BASE_URL}/raubzug/gegner/?searchuserid={user_id}"
    soup_confirm = client.get_url(url)
    csrf, gegnerid, attrs, disponivel = parsear_confirmacao_ataque(soup_confirm)

    if not disponivel:
        log.warning(f"Ataque em {user_id} cancelado — jogador indisponível")
        return {"status": "indisponivel", "user_id": user_id}

    if not gegnerid:
        log.warning(f"gegnerid não encontrado para {user_id}")
        return {"status": "erro", "user_id": user_id}

    # Atualiza cache com atributos frescos se disponível
    if attrs:
        cache = carregar_perfis_cache()
        if user_id in cache.get("perfis", {}):
            cache["perfis"][user_id].update(attrs)
            salvar_perfis_cache(cache)

    # Passo 2: POST para executar o ataque
    data = {
        "csrftoken": csrf,
        "ac": "raubzug",
        "sac": "attack",
        "gegnerid": gegnerid,
    }
    r = client.session.post(BASE_URL + "/", data=data, timeout=15)
    r.raise_for_status()
    soup_result = BeautifulSoup(r.text, "html.parser")

    # Verifica se o combate realmente ocorreu
    if "displayFightReport" not in r.text:
        # Tenta identificar o motivo
        motivo = "desconhecido"
        txt = r.text[:500].lower()
        if "imunidade" in txt or "immune" in txt:
            motivo = "alvo imune"
        elif "cooldown" in txt or "minutos" in txt or "secondscounter" in txt:
            motivo = "cooldown ativo"
        elif "login" in txt or "session" in txt or len(r.text) < 5000:
            motivo = "sessão expirada"  # página muito curta = provavelmente redirect para login
        elif "not found" in txt or "404" in txt:
            motivo = "página não encontrada"
        log.warning(f"displayFightReport ausente para {user_id} — motivo: {motivo}")
        # Salva HTML completo para debug
        try:
            from pathlib import Path as _P
            path_debug = _P(os.getcwd()) / "debug_ataque.html"
            path_debug.write_text(r.text, encoding="utf-8")  # arquivo completo, sem truncar
            # Tenta extrair mensagem útil do HTML
            soup_err = BeautifulSoup(r.text, "html.parser")
            # Procura mensagens de erro/aviso em divs comuns do KF
            for cls in ["kf-error", "error", "box-bg", "content"]:
                msg_err = soup_err.find("div", class_=cls)
                if msg_err:
                    txt = msg_err.get_text(strip=True)[:200]
                    if txt:
                        log.warning(f"  Servidor retornou: {txt}")
                        break
            log.warning(f"  HTML completo salvo em debug_ataque.html ({len(r.text)} bytes)")
        except Exception as e_dbg:
            log.debug(f"Erro ao salvar debug: {e_dbg}")
        return {"status": "indisponivel", "motivo": motivo, "user_id": user_id}

    resultado, gold_ganho, xp_ganho, turnos_stats = parsear_resultado_combate(soup_result, eu_fui_atacante=True)

    # Registra para aprendizado (usa atributos frescos se disponíveis, senão usa cache)
    perfil_aprendizado = attrs.copy() if attrs else {}
    perfil_aprendizado["user_id"] = user_id
    if not attrs:
        cache = carregar_perfis_cache()
        perfil_aprendizado.update(cache.get("perfis", {}).get(user_id, {}))
    # Adiciona nome e level do pig_list (parsear_confirmacao_ataque não retorna esses campos)
    pl = carregar_pig_list()
    if user_id in pl:
        pig_entry = pl[user_id]
        if not perfil_aprendizado.get("nome") and pig_entry.get("nome"):
            perfil_aprendizado["nome"] = pig_entry["nome"]
        if not perfil_aprendizado.get("level") and pig_entry.get("level"):
            perfil_aprendizado["level"] = pig_entry["level"]
        perfil_aprendizado["_score_cache"] = pig_entry.get("score_cache", 0)
    registrar_combate_srv(perfil_aprendizado, resultado, gold_ganho, xp_ganho,
                          dano_causado=turnos_stats.get("dano_eu", 0),
                          dano_recebido=turnos_stats.get("dano_adv", 0),
                          turnos=turnos_stats)

    estado = carregar_estado()
    registrar_ataque(estado, user_id, resultado, gold_ganho, xp_ganho)

    # Atualiza pig_list com resultado
    pig_list = carregar_pig_list()
    if user_id in pig_list:
        pig_list[user_id]["status"] = "atacado"
        pig_list[user_id]["atacado_em"] = agora().isoformat()
        pig_list[user_id]["resultado"] = resultado
        pig_list[user_id]["gold_ganho"] = gold_ganho
        pig_list[user_id]["xp_ganho"] = xp_ganho
        salvar_pig_list(pig_list)

    emoji = "✓" if resultado == "vitoria" else "✗"
    log.info(f"⚔ {emoji} {resultado.upper()} | +{gold_ganho}g | +{xp_ganho}xp")
    return {
        "status": "executado",
        "resultado": resultado,
        "gold_ganho": gold_ganho,
        "xp_ganho": xp_ganho,
        "attrs_alvo": attrs,
    }

# ═══════════════════════════════════════════
# BUSCA DE ALVO PARA IMUNIZAÇÃO
# ═══════════════════════════════════════════
def buscar_alvo_imunizacao(client, estado, score_min, excluir=None):
    """
    Usa o cache de perfis para encontrar candidatos sem HTTP.
    Só verifica disponibilidade (botão Attack) em tempo real.

    Ordem de preferência:
    1. Score mais alto (mais chance de ganhar)
    2. Level mais próximo do meu (menos variável)
    """
    candidatos = candidatos_imunizacao_do_cache(estado)

    if not candidatos:
        log.warning("Cache de perfis vazio — não é possível buscar alvo sem HTTP massivo")
        return None

    # Remove alvos já tentados nesta rodada
    if excluir:
        candidatos = [c for c in candidatos if c.get("user_id") not in excluir]
        if not candidatos:
            return None

    log.info(f"Candidatos imunização no cache: {len(candidatos)} (score_min={score_min})")

    meu_lv = MY_STATS.get("level", 22)

    # Filtra por score mínimo e perda de XP aceitável
    def xp_perda(c):
        delta = meu_lv - c.get("level", meu_lv)
        return max(0, delta - 5)

    # Busca progressiva: score >= 80, aumentando XP aceito de 0 até PERDA_XP_MAX
    # Checa todos os candidatos em cada nível de XP antes de relaxar
    validos = []
    xp_limite_max = max(PERDA_XP_MAX, 0)

    for xp_aceito in range(0, xp_limite_max + 1):
        candidatos_round = [c for c in candidatos
                            if c["score"] >= score_min
                            and xp_perda(c) <= xp_aceito]
        if candidatos_round:
            validos = candidatos_round
            if xp_aceito == 0:
                log.info(f"  Score >= {score_min} sem perder XP: {len(validos)} candidatos")
            else:
                log.info(f"  Score >= {score_min} aceitando -{xp_aceito} XP: {len(validos)} candidatos")
            break

    if not validos and score_min > 70:
        log.warning(f"  Nenhum candidato com score >= {score_min} — tentando com 70%...")
        for xp_aceito in range(0, xp_limite_max + 1):
            candidatos_round = [c for c in candidatos
                                if c["score"] >= 70
                                and xp_perda(c) <= xp_aceito]
            if candidatos_round:
                validos = candidatos_round
                log.info(f"  Fallback 70%: {len(validos)} candidatos com -{xp_aceito} XP")
                break

    if not validos:
        log.warning(f"  Nenhum candidato com score >= {score_min} disponível — ficando vulnerável")

    for c in validos[:20]:
        uid = c["user_id"]
        try:
            soup = client.get_url(f"{BASE_URL}/player/{uid}/")
            perfil = parsear_perfil(soup, uid)
        except Exception as e:
            log.warning(f"  Erro ao verificar {c['nome']}: {e}")
            continue

        if not perfil["disponivel"]:
            log.info(f"  {c['nome']} Lv{c['level']} — indisponível")
            time.sleep(0.5)
            continue

        log.info(f"  ✓ {c['nome']} Lv{c['level']} score={c['score']} disponível!")
        return perfil

    return None

# ═══════════════════════════════════════════
# RANKING
# ═══════════════════════════════════════════
def scrape_ranking(client, paginas=None):
    if paginas is None:
        # Gera lista de páginas com base em RANKING_MAX_PLAYERS (cada página = 100 jogadores)
        n = max(1, min(100, RANKING_MAX_PLAYERS // 100))
        paginas = [i * 100 for i in range(1, n + 1)]
    ts = agora().isoformat()
    soup_form = client.get("/highscore/spieler/")
    csrf = ""
    inp = soup_form.find("input", {"name": "csrftoken"})
    if inp:
        csrf = inp.get("value", "")

    jogadores = {}
    for pagina in paginas:
        log.info(f"  Ranking página {pagina}...")
        data = {
            "csrftoken": csrf, "ac": "highscore", "sac": "spieler",
            "filter": "beute", "clanfilter": "beute",
            "hsort": "0", "csort": "1", "viewtoggled": "0", "count": str(pagina),
        }
        try:
            soup = client.post("/", data=data)
        except Exception as e:
            log.warning(f"  Erro página {pagina}: {e}")
            continue

        count = 0
        for row in soup.find_all("tr", class_="highscore"):
            tds = row.find_all("td")
            if len(tds) < 8: continue
            try: pos = parse_num(tds[0].get_text())
            except: continue
            link = tds[1].find("a", href=True)
            if not link: continue
            nome = link.get_text(strip=True)
            uid_m = re.search(r"/player/(\d+)/", link["href"])
            uid = uid_m.group(1) if uid_m else ""
            if not uid: continue
            def td(i): return parse_num(tds[i].get_text()) if i < len(tds) else 0
            jogadores[uid] = {
                "posicao": pos, "nome": nome, "user_id": uid,
                "level": td(2), "preciosidades": td(3),
                "combates": td(4), "vitorias": td(5),
                "derrotas": td(6), "empates": td(7),
                "ouro_ganho": td(8), "ouro_perdido": td(9),
                "timestamp": ts,
            }
            count += 1
        log.info(f"  Página {pagina}: {count} jogadores")
        time.sleep(2)

    log.info(f"Ranking completo: {len(jogadores)} jogadores")
    return jogadores

def salvar_snapshot(jogadores):
    snapshots = []
    if os.path.exists(RANKING_FILE):
        try:
            with open(RANKING_FILE, encoding="utf-8") as f:
                snapshots = json.load(f)
        except (json.JSONDecodeError, ValueError):
            log.warning(f"[snapshot] {RANKING_FILE} corrompido — reiniciando arquivo")
            snapshots = []
    snapshots.append({"timestamp": agora().isoformat(), "jogadores": jogadores})
    snapshots = snapshots[-50:]
    with open(RANKING_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, indent=2, ensure_ascii=False)

def carregar_snapshots():
    if not os.path.exists(RANKING_FILE): return []
    try:
        with open(RANKING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return []

# ═══════════════════════════════════════════
# PIG LIST
# ═══════════════════════════════════════════
def carregar_pig_list():
    if os.path.exists(PIG_LIST_FILE):
        try:
            with open(PIG_LIST_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            bak = PIG_LIST_FILE + ".bak"
            if os.path.exists(bak):
                try:
                    with open(bak, encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
    return {}

def salvar_pig_list(pig_list):
    tmp = PIG_LIST_FILE + ".tmp"
    bak = PIG_LIST_FILE + ".bak"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pig_list, f, indent=2, ensure_ascii=False)
    if os.path.exists(PIG_LIST_FILE):
        try:
            os.replace(PIG_LIST_FILE, bak)
        except OSError:
            pass
    os.replace(tmp, PIG_LIST_FILE)

def atualizar_pig_list(pig_list, jogadores_ant, jogadores_atu, estado):
    """
    Regras simples e claras:

    ADICIONAR à lista:
      1. dp > 0 (ouro_perdido aumentou) E dd > 0 (derrotas aumentaram)
         → gold_esperado = dp / dd
         → só adiciona se gold_esperado >= GOLD_MIN_PIG (50g)

      2. dp > 0 mas dd == 0 (perdeu ouro sem derrota nova — raro, talvez ataque recente)
         → gold_esperado = dp
         → só adiciona se gold_esperado >= GOLD_MIN_PIG

      3. dprec > 0 (preciosidades aumentaram) sem aumento de combates
         → gold_esperado = dprec * 10  (cada preciosidade ~ 10g de missão)
         → só adiciona se gold_esperado >= GOLD_MIN_PIG

    REMOVER da lista:
      - dd > 0 E dp == 0 (derrota nova sem aumento de ouro-) → zerou, remove
      - Já está na lista, sem nenhum sinal novo por 24h → remove (opcional)

    ATUALIZAR gold_esperado quando já está na lista:
      - Se novos dp/dd → recalcula acumulado
    """
    agora_iso = agora().isoformat()
    adicionados = removidos = 0
    hist_ataques = estado.get("historico_ataques", {})

    for uid, j in jogadores_atu.items():
        if uid == MY_USER_ID:
            continue
        if uid not in jogadores_ant:
            continue

        a     = jogadores_ant[uid]
        dd    = j["derrotas"]     - a["derrotas"]      # diferença de derrotas
        dp    = j["ouro_perdido"] - a["ouro_perdido"]  # diferença de ouro perdido
        dprec = j["preciosidades"]- a["preciosidades"] # diferença de preciosidades
        wr    = round(j["vitorias"] / j["combates"] * 100, 1) if j.get("combates", 0) > 0 else 0

        meu_ataque_iso    = hist_ataques.get(uid)
        eu_ataquei_recente = meu_ataque_iso and seg_desde(meu_ataque_iso) < BLOQUEIO_MESMO_PLAYER

        # ── Se eu ataquei recentemente → gerencia histórico ──────────────────
        if eu_ataquei_recente:
            if uid in pig_list and pig_list[uid].get("status") != "atacado":
                pig_list[uid]["status"]     = "atacado"
                pig_list[uid]["atacado_em"] = meu_ataque_iso
            continue

        # ── Após 12h do meu ataque: decide se volta ou sai ───────────────────
        if uid in pig_list and pig_list[uid].get("status") == "atacado":
            h = seg_desde(pig_list[uid].get("atacado_em", agora_iso)) / 3600
            if h >= 12:
                if dp > 0 or (dd > 0 and dp > 0) or dprec > 0:
                    pig_list[uid]["status"]       = "ativo"
                    pig_list[uid]["detectado_em"] = agora_iso
                    log.info(f"  ↩ REATIVADO: {j['nome']}")
                else:
                    del pig_list[uid]; removidos += 1
            continue

        # ── REMOVER: derrota nova mas ouro_perdido não subiu → zerou ─────────
        if uid in pig_list and pig_list[uid].get("status", "ativo") == "ativo":
            if dd > 0 and dp == 0:
                log.info(f"  - ZEROU: {j['nome']} (derrota +{dd} mas ouro_perdido não subiu → zerou)")
                del pig_list[uid]; removidos += 1
                continue
            # Atualiza gold_esperado se novos sinais
            if dp > 0:
                pig_list[uid]["delta_ouro_perdido"] = pig_list[uid].get("delta_ouro_perdido", 0) + dp
                pig_list[uid]["delta_derrotas"]     = pig_list[uid].get("delta_derrotas", 0) + dd
                dd_t = pig_list[uid]["delta_derrotas"]
                dp_t = pig_list[uid]["delta_ouro_perdido"]
                pig_list[uid]["gold_esperado"] = round(dp_t / dd_t) if dd_t > 0 else dp_t
                log.info(f"  ~ ATUALIZADO: {j['nome']} gold_esperado={pig_list[uid]['gold_esperado']}g")
            continue

        # ── ADICIONAR: não está na lista ──────────────────────────────────────
        base = {
            "nome": j["nome"], "user_id": uid, "level": j["level"], "win_rate": wr,
            "preciosidades": j["preciosidades"],
            "ouro_ganho": j["ouro_ganho"], "ouro_perdido": j["ouro_perdido"],
            "detectado_em": agora_iso, "tentativas": 0, "ultimo_check": None,
            "status": "ativo",
        }

        # Caso 1 e 2: ouro_perdido aumentou
        if dp > 0:
            if dd > 0:
                gold_esp = round(dp / dd)
                motivo   = f"+{dd} derrota(s), +{dp}g ouro- → {gold_esp}g/derrota"
                categoria = "PIG_CONFIRMADO"
            else:
                gold_esp  = dp
                motivo    = f"+{dp}g ouro- (sem derrota nova)"
                categoria = "PIG_CONFIRMADO"

            if gold_esp < GOLD_MIN_PIG:
                log.debug(f"  ~ IGNORADO: {j['nome']} gold_esp={gold_esp}g < {GOLD_MIN_PIG}g")
                continue

            base["categoria"]         = categoria
            base["gold_esperado"]     = gold_esp
            base["delta_ouro_perdido"]= dp
            base["delta_derrotas"]    = dd
            base["motivos"]           = [motivo]
            pig_list[uid] = base; adicionados += 1
            log.info(f"  + {categoria}: {j['nome']} Lv{j['level']} | {motivo}")

        # Caso 3: preciosidades aumentaram (terminou missão, tem gold)
        elif dprec > 0:
            gold_esp = dprec * 10  # ~10g por preciosidade
            if gold_esp < GOLD_MIN_PIG:
                log.debug(f"  ~ IGNORADO: {j['nome']} prec={dprec} → {gold_esp}g < {GOLD_MIN_PIG}g")
                continue

            base["categoria"]          = "PIG_PROVAVEL"
            base["gold_esperado"]      = gold_esp
            base["delta_ouro_perdido"] = 0
            base["delta_derrotas"]     = 0
            base["motivos"]            = [f"+{dprec} prec → ~{gold_esp}g estimado"]
            pig_list[uid] = base; adicionados += 1
            log.info(f"  + PIG_PROVAVEL: {j['nome']} Lv{j['level']} | +{dprec} prec → ~{gold_esp}g")

    log.info(f"Pig list: {len(pig_list)} total | +{adicionados} adicionados | -{removidos} removidos")
    return pig_list


# ═══════════════════════════════════════════
# RAUBZUG — estado de CD
# ═══════════════════════════════════════════
def rezar_altar(client):
    """
    Reza no altar para recuperar HP.
    O jogo mostra o máximo de gold que pode ser doado (recupera HP ao máximo).
    Faz uma única requisição com o máximo disponível na página.
    """
    try:
        soup = client.get("/landsitz/altar/", fragment=False)

        # Extrai o máximo de gold disponível no select
        select = soup.find("select", {"name": "goldspende"})
        if not select:
            log.warning("Altar: select não encontrado")
            return False

        opcoes = [int(o["value"]) for o in select.find_all("option") if o.get("value","").isdigit()]
        if not opcoes:
            log.warning("Altar: nenhuma opção encontrada")
            return False

        max_gold = max(opcoes)

        # Extrai csrftoken
        csrf = ""
        token_input = soup.find("input", {"name": "csrftoken"})
        if token_input:
            csrf = token_input.get("value", "")

        if not csrf:
            log.warning("Altar: csrftoken não encontrado")
            return False

        # Reza com o máximo de gold
        r2 = client.post("/", data={
            "ac": "landsitz",
            "sac": "altar",
            "csrftoken": csrf,
            "goldspende": str(max_gold),
        })

        if "altar" in r2.url or r2.status_code == 200:
            log.info(f"Altar: rezou com {max_gold} gold — HP recuperado!")
            return True
        else:
            log.warning(f"Altar: resposta inesperada {r2.status_code}")
            return False

    except Exception as e:
        log.error(f"Altar: erro — {e}")
        return False


def esta_fora_horario():
    """Retorna True se o horário atual está FORA da janela de operação configurada."""
    if not HORARIO_ATIVO:
        return False
    from datetime import datetime as _dt
    agora_local = _dt.now()
    try:
        h_ini, m_ini = map(int, HORARIO_INICIO.split(":"))
        h_par, m_par = map(int, HORARIO_PARADA.split(":"))
    except Exception:
        return False
    minutos_agora = agora_local.hour * 60 + agora_local.minute
    minutos_ini   = h_ini * 60 + m_ini
    minutos_par   = h_par * 60 + m_par
    if minutos_ini < minutos_par:
        return not (minutos_ini <= minutos_agora < minutos_par)
    else:  # janela cruza meia-noite (improvável mas suportado)
        return not (minutos_agora >= minutos_ini or minutos_agora < minutos_par)


def calcular_horas_ate_inicio():
    """Retorna float: horas até o próximo HORARIO_INICIO."""
    from datetime import datetime as _dt, timedelta as _td
    agora_local = _dt.now()
    try:
        h, m = map(int, HORARIO_INICIO.split(":"))
    except Exception:
        return 8.0
    inicio = agora_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if inicio <= agora_local:
        inicio += _td(days=1)
    return (inicio - agora_local).total_seconds() / 3600


def comprar_armadura_barata(client):
    """
    Compra o máximo possível da armadura disponível em /shop/ruestungen/.
    A página já é o formulário de compra direto (não há listagem separada).
    Extrai preço de <input id="costs_gold">, nome do texto da confirmação.
    Retorna (qtd_comprada, preco_unitario, nome).
    """
    gold_atual, _ = parsear_gold_gems(client)
    if gold_atual <= 0:
        log.info("  Comprar armadura: sem gold")
        return 0, 0, ""

    try:
        soup_list = client.get("/shop/ruestungen/", fragment=False)
    except Exception as e:
        log.warning(f"  Comprar armadura: erro ao carregar loja — {e}")
        return 0, 0, ""

    if _esta_bloqueado_por_missao(soup_list):
        log.debug("  Comprar armadura: bloqueado por missão ativa")
        return 0, 0, ""

    # Encontra o buy link mais barato disponível
    buy_a = None
    melhor_preco = 999999
    for tr in soup_list.find_all("tr", class_="mobile-cols-2"):
        a = tr.find("a", href=lambda h: h and "wac=buy" in h)
        if not a:
            continue
        # Extrai preço da listagem para escolher o mais barato
        span_gold = None
        for span in tr.find_all("span"):
            if span.find("img", src=lambda s: s and "goldstueck.gif" in s):
                span_gold = span; break
        preco_list = 0
        if span_gold:
            m = re.search(r"[\d.,]+", span_gold.get_text())
            if m:
                preco_list = int(m.group().replace(".", "").replace(",", ""))
        if preco_list < melhor_preco:
            melhor_preco = preco_list
            buy_a = a

    if not buy_a:
        log.info("  Comprar armadura: nenhuma armadura disponível na loja")
        return 0, 0, ""

    # Segue buy link para página de confirmação
    buy_url = buy_a["href"]
    if buy_url.startswith("http"):
        from urllib.parse import urlparse as _up
        buy_url = _up(buy_url).path + ("?" + _up(buy_a["href"]).query if _up(buy_a["href"]).query else "")
    try:
        soup = client.get(buy_url, fragment=False)
    except Exception as e:
        log.warning(f"  Comprar armadura: erro ao carregar confirmação — {e}")
        return 0, 0, ""

    form = soup.find("form")
    costs_el = soup.find(id="costs_gold")
    if not form or not costs_el:
        log.warning("  Comprar armadura: formulário/costs_gold não encontrado na confirmação")
        return 0, 0, ""

    try:
        preco = int(costs_el.get("value", "0").replace(".", "").replace(",", ""))
    except ValueError:
        log.warning("  Comprar armadura: preço inválido")
        return 0, 0, ""

    if preco <= 0:
        log.warning("  Comprar armadura: preço zero")
        return 0, 0, ""

    if gold_atual < preco:
        log.info(f"  Comprar armadura: gold ({gold_atual}g) < preço ({preco}g)")
        return 0, preco, ""

    # Nome do item
    nome = "Armadura"
    txt_pagina = soup.get_text()
    for pat in [r"purchase this armour \(([^)]+)\)", r"diese Rüstung \(([^)]+)\)",
                r"armadura \(([^)]+)\)", r"armure \(([^)]+)\)"]:
        m_nome = re.search(pat, txt_pagina, re.IGNORECASE)
        if m_nome:
            nome = m_nome.group(1).strip(); break

    qtd = min(gold_atual // preco, 999)
    log.info(f"  Comprando {qtd}x {nome} @ {preco}g (gold: {gold_atual}g)...")

    campos = {}
    for inp in form.find_all("input"):
        n = inp.get("name")
        if n:
            campos[n] = inp.get("value", "")
    campos["amount"] = str(qtd)
    campos["buy"] = "1"

    action = form.get("action") or buy_url
    if action.startswith("http"):
        from urllib.parse import urlparse as _up2
        action = _up2(action).path
    if not action or action == "#":
        action = buy_url

    try:
        client.post(action, data=campos, fragment=False)
    except Exception as e:
        log.warning(f"  Compra armadura: erro no POST — {e}")
        return 0, preco, nome

    log.info(f"  ✓ Comprou {qtd}x {nome} (gastou ~{qtd * preco}g)")
    return qtd, preco, nome


def _parsear_shop_listagem(soup, tipo):
    """
    Analisa página de listagem de loja (/shop/waffen/ ou /shop/schilde/).
    Retorna dict {nome, gold_necessario, url_compra, categoria} para o PRIMEIRO item
    com skill atendida (tem link wac=buy) e preço em gold (não gema).
    """
    for tr in soup.find_all("tr", class_="mobile-cols-2"):
        # Precisa de link de compra (skill OK)
        buy_a = tr.find("a", href=lambda h: h and "wac=buy" in h)
        if not buy_a:
            continue
        # Precisa ter goldstueck (preço em gold)
        if not tr.find("img", src=lambda s: s and "goldstueck.gif" in s):
            continue
        # Não pode ter preço em gema
        if tr.find("img", src=lambda s: s and ("edelstein.gif" in s or "coin.png" in s)):
            continue

        # Extrai preço: procura número na span que contém goldstueck
        preco = 0
        for span in tr.find_all("span"):
            if not span.find("img", src=lambda s: s and "goldstueck.gif" in s):
                continue
            m = re.search(r"[\d.,]+", span.get_text())
            if m:
                preco = int(m.group().replace(".", "").replace(",", ""))
                break
        # Fallback: primeiro número do tr
        if preco == 0:
            m = re.search(r"\b(\d[\d.,]+)\b", tr.get_text())
            if m:
                preco = int(m.group(1).replace(".", "").replace(",", ""))

        # Nome: tag <strong> ou <b> da td de info (class "t"), não da td com o botão
        nome = "Item"
        strong = tr.find("strong") or tr.find("b")
        if strong:
            nome = strong.get_text(strip=True)[:80]
        else:
            info_td = tr.find("td", class_=lambda c: c and "t" in (c if isinstance(c, str) else " ".join(c)).split())
            if not info_td:
                tds = tr.find_all("td")
                info_td = tds[1] if len(tds) > 1 else (tds[0] if tds else None)
            if info_td:
                nome = re.sub(r"\s+", " ", info_td.get_text(separator=" ", strip=True)).strip()[:80]

        url_compra = buy_a["href"]
        # Normaliza para path relativo preservando query string
        if url_compra.startswith("http"):
            from urllib.parse import urlparse as _up
            _p = _up(url_compra)
            url_compra = _p.path + ("?" + _p.query if _p.query else "")
        # Extrai nível de requisito (para comparação de upgrade em aneis/amuletos)
        req_level = 0
        m_rl = re.search(r"(?:level|n[íi]vel|stufe)\s*[:\-]?\s*(\d+)", tr.get_text(separator=" "), re.IGNORECASE)
        if m_rl:
            req_level = int(m_rl.group(1))
        return {"nome": nome, "gold_necessario": preco, "url_compra": url_compra, "categoria": tipo, "req_level": req_level}

    return None


def _parsear_shop_todos_itens(soup, tipo):
    """
    Analisa página de listagem de loja e retorna TODOS os itens (com e sem buy link).
    Cada item é um dict com:
      nome, gold, req_skill_tipo, req_skill_valor, req_level,
      pode_comprar, url_compra, categoria
    """
    # Mapeamento de variantes de texto para tipo de skill interno
    _SKILL_MAP = [
        # PT — "Skills de duas mãos", "Skills de armadura", etc.
        (r"duas m[aã]os", "zweihand"),
        (r"arma de 2 m[aã]os", "zweihand"),
        (r"uma m[aã]o", "einhand"),
        (r"arma de 1 m[aã]o", "einhand"),
        (r"espada", "einhand"),
        (r"skills de armadura", "ruestung"),
        (r"armadura", "ruestung"),
        # DE
        (r"zweihand", "zweihand"),
        (r"einhand", "einhand"),
        (r"r[uü]stung", "ruestung"),
        # EN — "Two-hander skills", "One-hander skills", "Armour skills"
        (r"two.?hand", "zweihand"),
        (r"one.?hand", "einhand"),
        (r"armou?r", "ruestung"),
        # ES
        (r"dos manos", "zweihand"),
        (r"una mano", "einhand"),
        (r"armadura", "ruestung"),
    ]

    itens = []
    for tr in soup.find_all("tr", class_="mobile-cols-2"):
        # Verifica se tem preço em gold
        if not tr.find("img", src=lambda s: s and "goldstueck.gif" in s):
            continue
        # Pula itens que custam gema
        if tr.find("img", src=lambda s: s and ("edelstein.gif" in s or "coin.png" in s)):
            continue

        # Extrai preço de compra — pega o MAIOR valor entre todos os spans com ícone de gold
        # (evita capturar preço de venda pequeno que aparece antes do preço de compra no HTML)
        gold = 0
        gold_candidatos = []
        for span in tr.find_all("span"):
            if not span.find("img", src=lambda s: s and "goldstueck.gif" in s):
                continue
            m = re.search(r"[\d.,]+", span.get_text())
            if m:
                val = int(m.group().replace(".", "").replace(",", ""))
                if val > 0:
                    gold_candidatos.append(val)
        if gold_candidatos:
            gold = max(gold_candidatos)
        if gold == 0:
            m = re.search(r"\b(\d[\d.,]+)\b", tr.get_text())
            if m:
                gold = int(m.group(1).replace(".", "").replace(",", ""))
        # Preços de itens no jogo custam no mínimo 50g — valor menor indica erro de parsing
        if 0 < gold < 50:
            log.debug(f"  Loja: preço {gold}g ignorado (< 50g, provável erro de parsing)")
            gold = 0

        # Extrai nome
        nome = "Item"
        strong = tr.find("strong") or tr.find("b")
        if strong:
            nome = strong.get_text(strip=True)[:80]
        else:
            info_td = tr.find("td", class_=lambda c: c and "t" in (c if isinstance(c, str) else " ".join(c)).split())
            if not info_td:
                tds = tr.find_all("td")
                info_td = tds[1] if len(tds) > 1 else (tds[0] if tds else None)
            if info_td:
                nome = re.sub(r"\s+", " ", info_td.get_text(separator=" ", strip=True)).strip()[:80]

        # Extrai requisitos do texto do TR
        tr_text = tr.get_text(separator=" ", strip=True)
        req_skill_tipo = None
        req_skill_valor = 0
        req_level = 0

        # Level requirement
        m_lv = re.search(r"(?:level|nível|stufe)\s*[:\-]?\s*(\d+)", tr_text, re.IGNORECASE)
        if m_lv:
            req_level = int(m_lv.group(1))

        # Skill requirement
        # Padrão: "Condição - Skill: N" / "Requirement - Skill: N" / "Voraussetzung - Skill: N"
        m_sk = re.search(
            r"(?:condi[çc][aã]o|requirement|voraussetzung|condition|requisito|pr[eé]requis)\s*[-–]\s*([^:]+):\s*(\d+)",
            tr_text, re.IGNORECASE
        )
        if m_sk:
            skill_txt = m_sk.group(1).strip()
            req_skill_valor = int(m_sk.group(2))
            for pattern, sk_tipo in _SKILL_MAP:
                if re.search(pattern, skill_txt, re.IGNORECASE):
                    req_skill_tipo = sk_tipo
                    break

        # Verifica compra
        buy_a = tr.find("a", href=lambda h: h and "wac=buy" in h)
        pode_comprar = buy_a is not None
        url_compra = None
        if buy_a:
            url_compra = buy_a["href"]
            if url_compra.startswith("http"):
                from urllib.parse import urlparse as _up2
                _p2 = _up2(url_compra)
                url_compra = _p2.path + ("?" + _p2.query if _p2.query else "")

        # Verifica venda — item no inventário tem sell link e "Item value: N"
        sell_a = tr.find("a", href=lambda h: h and "/shop/sell/" in h)
        url_venda = None
        gold_venda = 0
        equipado = False
        if sell_a:
            url_venda = sell_a["href"]
            if url_venda.startswith("http"):
                from urllib.parse import urlparse as _up
                url_venda = _up(url_venda).path + ("?" + _up(sell_a["href"]).query if _up(sell_a["href"]).query else "")
            m_val = re.search(r"[Ii]tem\s+value[:\s]+(\d[\d.,]+)", tr_text)
            if not m_val:
                m_val = re.search(r"[Ww]ert[:\s]+(\d[\d.,]+)", tr_text)  # DE
            if not m_val:
                m_val = re.search(r"[Vv]alor\s+da\s+mercadoria[:\s]+(\d[\d.,]+)", tr_text)  # PT inventário
            if not m_val:
                m_val = re.search(r"[Pp]re[çc]o\s+de\s+venda[:\s]+(\d[\d.,]+)", tr_text)  # PT
            if not m_val:
                m_val = re.search(r"[Vv]alor\s+do\s+item[:\s]+(\d[\d.,]+)", tr_text)  # PT alt
            if not m_val:
                m_val = re.search(r"[Vv]alor[:\s]+(\d[\d.,]+)", tr_text)  # PT genérico
            if m_val:
                gold_venda = int(m_val.group(1).replace(".", "").replace(",", ""))
            # Na listagem da loja, qualquer item com sell link está equipado
            # (itens do inventário não-equipados aparecem na seção de inventário, não aqui)
            equipado = True

        itens.append({
            "nome": nome,
            "gold": gold,
            "req_skill_tipo": req_skill_tipo,
            "req_skill_valor": req_skill_valor,
            "req_level": req_level,
            "pode_comprar": pode_comprar,
            "url_compra": url_compra,
            "url_venda": url_venda,
            "gold_venda": gold_venda,
            "equipado": equipado,
            "categoria": tipo,
        })

    return itens


_CATALOGO_CACHE = None


def _carregar_catalogo():
    """Carrega catálogo local de itens (Itens/*.txt). Resultado cacheado em memória.
    Retorna dict: chave → lista de dicts com nome, gold, req_skill, req_skill_tipo, categoria
    ordenada por req_skill ascendente.
    """
    global _CATALOGO_CACHE
    if _CATALOGO_CACHE is not None:
        return _CATALOGO_CACHE

    base = Path(__file__).parent / "Itens"

    def _parse_file(fname, col_gold, col_req, req_tipo, categoria, req_fn=None, filtro_gema=False):
        """filtro_gema=True: após ordenar por req_skill, remove itens cujo preço é inferior
        ao item anterior (req menor). Itens de gema quebram a progressão crescente de preço
        — uma arma com req=76 não pode custar 1900g se a de req=72 custa 31500g.
        Funciona em qualquer faixa de req, não depende de threshold fixo."""
        itens = []
        path = base / fname
        if not path.exists():
            log.warning(f"  Catálogo: arquivo não encontrado — {path}")
            return itens
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            log.warning(f"  Catálogo: erro ao ler {fname} — {e}")
            return itens
        for line in lines[1:]:  # pula cabeçalho
            cols = [c.strip() for c in line.split(",")]
            if len(cols) <= max(col_gold, col_req):
                continue
            nome = cols[0]
            if not nome or "[Bazar]" in nome:
                continue
            try:
                gold = int(cols[col_gold].replace(".", "").replace(" ", ""))
            except ValueError:
                continue
            if gold <= 0:
                continue
            try:
                req = req_fn(cols[col_req]) if req_fn else int(cols[col_req])
            except (ValueError, TypeError, AttributeError):
                req = 0
            itens.append({
                "nome": nome,
                "gold": gold,
                "req_skill": req,
                "req_skill_tipo": req_tipo,
                "categoria": categoria,
            })
        itens.sort(key=lambda x: x["req_skill"])
        if filtro_gema and len(itens) > 1:
            # Remove itens que quebram a progressão crescente de preço:
            # itera do mais barato (req menor) para o mais caro, mantendo o
            # máximo de gold visto até agora. Se o item atual custa menos que
            # o máximo anterior, é item de gema disfarçado e deve ser pulado.
            filtrados = []
            gold_max = 0
            for item in itens:
                if item["gold"] >= gold_max:
                    gold_max = item["gold"]
                    filtrados.append(item)
                else:
                    log.debug(f"  Catálogo {fname}: pulando item de gema '{item['nome']}' "
                              f"(req={item['req_skill']}, gold={item['gold']} < max_anterior={gold_max})")
            itens = filtrados
        return itens

    def _level_req(s):
        m = re.search(r"(\d+)", s)
        return int(m.group(1)) if m else 0

    _CATALOGO_CACHE = {
        "waffen_einhand":  _parse_file("1h.txt",       6, 7, "einhand",  "waffen",  filtro_gema=True),
        "waffen_zweihand": _parse_file("2h.txt",       6, 7, "zweihand", "waffen",  filtro_gema=True),
        "ruestungen":      _parse_file("armadura.txt", 4, 5, "ruestung", "ruestungen"),
        "schilde":         _parse_file("escudos.txt",  6, 7, "ruestung", "schilde"),
        "aneis":           _parse_file("aneis.txt",    6, 7, None,       "aneis",    req_fn=_level_req),
        "amuletos":        _parse_file("amuletos.txt", 7, 8, None,       "amuletos"),
    }
    totais = ", ".join(f"{k}={len(v)}" for k, v in _CATALOGO_CACHE.items())
    log.debug(f"  Catálogo carregado: {totais}")
    return _CATALOGO_CACHE


def verificar_alvo_equipamento(client, estado):
    """
    Determina o próximo item a comprar usando o catálogo local (Itens/*.txt).
    Escaneia a loja apenas para detectar o item equipado atual e o buy link quando disponível.
    Salva em estado["item_alvo"] o melhor item atingível (maior req_skill dentro da skill atual).
    - 2h: waffen_zweihand + ruestungen
    - 1h: waffen_einhand + schilde + ruestungen
    """
    if not COMPRAR_EQUIPAMENTO:
        return
    paginas = [("/shop/waffen/", "waffen"), ("/shop/ruestungen/", "ruestungen")]
    if BUILD_TIPO == "1h":
        paginas.insert(1, ("/shop/schilde/", "schilde"))

    # Skills atuais do personagem
    sk_2maos    = MY_STATS.get("sk_2maos", 0)
    sk_1mao     = MY_STATS.get("sk_1mao", 0)
    sk_armadura = MY_STATS.get("sk_armadura", 0)

    catalogo = _carregar_catalogo()

    candidatos    = []  # buy link disponível (gold suficiente)
    ouro_bloqueados = []  # gold insuficiente — acumula ouro

    algum_shop_acessivel = False
    for url_loja, tipo in paginas:
        try:
            soup = client.get(url_loja, fragment=False)
        except Exception as e:
            log.warning(f"  Loja {tipo}: erro ao carregar — {e}")
            continue

        # Dump diagnóstico da loja (apenas uma vez por perfil)
        _dump_loja = os.path.join(os.path.dirname(ESTADO_FILE), f"debug_loja_{tipo}.html")
        if not os.path.exists(_dump_loja):
            try:
                with open(_dump_loja, "w", encoding="utf-8") as _f:
                    _f.write(str(soup))
                log.info(f"  [DIAG] HTML da loja {tipo} salvo em {_dump_loja}")
            except Exception:
                pass

        if _esta_bloqueado_por_missao(soup):
            log.debug(f"  Loja {tipo}: bloqueada por missão ativa — pulando scan")
            continue

        algum_shop_acessivel = True
        todos = _parsear_shop_todos_itens(soup, tipo)

        # Item equipado atualmente (tem sell link na listagem da loja)
        item_eq = next((i for i in todos if i.get("equipado")), None)
        gold_venda_eq = item_eq["gold_venda"] if item_eq else 0
        url_venda_eq  = item_eq["url_venda"]  if item_eq else None
        req_eq = item_eq.get("req_skill_valor", 0) if item_eq else 0
        urgente = item_eq is None
        if item_eq:
            log.debug(f"  Loja {tipo}: equipado='{item_eq['nome']}' req={req_eq} venda={gold_venda_eq}g")

        # Chave do catálogo e skill correspondente ao tipo de loja
        if tipo == "waffen":
            cat_key  = "waffen_zweihand" if BUILD_TIPO == "2h" else "waffen_einhand"
            sk_atual = sk_2maos if BUILD_TIPO == "2h" else sk_1mao
            req_tipo = "zweihand" if BUILD_TIPO == "2h" else "einhand"
        elif tipo == "ruestungen":
            cat_key  = "ruestungen"
            sk_atual = sk_armadura
            req_tipo = "ruestung"
        elif tipo == "schilde":
            cat_key  = "schilde"
            sk_atual = sk_armadura
            req_tipo = "ruestung"
        else:
            continue

        # Sem skill de armadura: req=0 items dão 0 defesa — inúteis, pulamos completamente
        if tipo in ("ruestungen", "schilde") and sk_armadura == 0:
            log.debug(f"  Catálogo {cat_key}: sk_armadura=0 — pulando (itens req=0 dão 0 defesa)")
            continue

        # Melhores itens do catálogo que o personagem pode usar E que são upgrade do equipado
        disponiveis = [
            it for it in catalogo.get(cat_key, [])
            if it["req_skill"] <= sk_atual and (urgente or it["req_skill"] > req_eq)
        ]
        if not disponiveis:
            log.debug(f"  Catálogo {cat_key}: nenhum item disponível (sk={sk_atual}, req_eq={req_eq})")
            continue

        # Melhor = maior req_skill; empate → maior gold (item mais caro = melhor em KF)
        melhor_cat = max(disponiveis, key=lambda x: (x["req_skill"], x["gold"]))
        gold_liquido = max(0, melhor_cat["gold"] - gold_venda_eq)

        # Busca buy link na listagem da loja para este item específico
        # Match por req_skill_valor + gold (robusto entre idiomas; nome varia por servidor)
        shop_match = next(
            (i for i in todos
             if not i.get("equipado")
             and i.get("req_skill_valor") == melhor_cat["req_skill"]
             and abs(i["gold"] - melhor_cat["gold"]) <= 50
             and i.get("pode_comprar")),
            None
        )
        url_compra = shop_match["url_compra"] if shop_match else None

        item_dict = {
            "nome":             melhor_cat["nome"],
            "gold_necessario":  gold_liquido,
            "gold_bruto":       melhor_cat["gold"],
            "gold_venda_atual": gold_venda_eq,
            "url_compra":       url_compra,
            "url_venda_atual":  url_venda_eq,
            "categoria":        tipo,
            "req_skill_valor":  melhor_cat["req_skill"],
            "req_skill_tipo":   req_tipo,
            "urgente":          urgente,
        }

        if url_compra:
            candidatos.append(item_dict)
            log.debug(f"  Catálogo {cat_key}: alvo '{melhor_cat['nome']}' req={melhor_cat['req_skill']} "
                      f"{melhor_cat['gold']}g (buy link disponível)")
        else:
            ouro_bloqueados.append(item_dict)
            log.debug(f"  Catálogo {cat_key}: alvo '{melhor_cat['nome']}' req={melhor_cat['req_skill']} "
                      f"{melhor_cat['gold']}g (acumulando gold)")

    # Determina item_alvo — por categoria pega o melhor (maior req_skill/gold_bruto),
    # depois entre categorias pega o MAIS BARATO (gold_necessario) para comprar logo
    # e equipar todas as categorias o mais rápido possível.
    def _melhor_da_lista(lista):
        """Por categoria: melhor item (max req_skill_valor, gold_bruto).
        Entre categorias: slots vazios (urgente) têm prioridade; empate → mais barato."""
        por_cat = {}
        for c in lista:
            cat = c["categoria"]
            prev = por_cat.get(cat)
            c_key = (c.get("req_skill_valor", 0), c["gold_bruto"])
            if prev is None or c_key > (prev.get("req_skill_valor", 0), prev["gold_bruto"]):
                por_cat[cat] = c
        if not por_cat:
            return None
        # Slots vazios têm prioridade absoluta sobre upgrades
        urgentes = [v for v in por_cat.values() if v.get("urgente")]
        if urgentes:
            return min(urgentes, key=lambda x: x["gold_necessario"])
        return min(por_cat.values(), key=lambda x: x["gold_necessario"])

    if candidatos:
        log.debug(f"  Candidatos ({len(candidatos)}): " +
                  ", ".join(
                      f"{c['nome']} req={c.get('req_skill_valor',0)} {c['gold_bruto']}g"
                      for c in sorted(candidatos, key=lambda x: (x.get("req_skill_valor", 0), x["gold_bruto"]), reverse=True)
                  ))
    if ouro_bloqueados:
        log.debug(f"  Gold-bloqueados ({len(ouro_bloqueados)}): " +
                  ", ".join(
                      f"{o['nome']} req={o.get('req_skill_valor',0)} {o['gold_bruto']}g"
                      for o in sorted(ouro_bloqueados, key=lambda x: (x.get("req_skill_valor", 0), x["gold_bruto"]), reverse=True)
                  ))

    if not algum_shop_acessivel:
        log.debug("  Alvo equipamento: todos shops bloqueados — mantendo item_alvo anterior")
        return

    # Urgentes em ouro_bloqueados (slot vazio, sem buy link ainda) têm prioridade
    # sobre candidatos não-urgentes — preencher slot vazio é mais importante do que
    # comprar upgrade quando o personagem já tem algo equipado.
    urgentes_bloqueados = [o for o in ouro_bloqueados if o.get("urgente")]

    if urgentes_bloqueados:
        melhor = min(urgentes_bloqueados, key=lambda x: x["gold_necessario"])
        alvo_anterior = estado.get("item_alvo")
        if not alvo_anterior or alvo_anterior.get("nome") != melhor["nome"]:
            log.info(
                f"  Alvo equipamento (slot vazio — acumulando gold): {melhor['nome']} "
                f"@ {melhor['gold_necessario']}g líquido "
                f"({melhor['gold_bruto']}g bruto, req_skill={melhor.get('req_skill_valor',0)}, cat={melhor['categoria']})"
            )
    elif candidatos:
        melhor = _melhor_da_lista(candidatos)
        alvo_anterior = estado.get("item_alvo")
        if not alvo_anterior or alvo_anterior.get("nome") != melhor["nome"]:
            log.info(
                f"  Alvo de equipamento: {melhor['nome']} @ {melhor['gold_necessario']}g líquido "
                f"({melhor['gold_bruto']}g bruto, req_skill={melhor.get('req_skill_valor',0)}, cat={melhor['categoria']})"
            )
    elif ouro_bloqueados:
        # Tem skill mas não tem gold ainda — salva alvo para acumular ouro
        melhor = _melhor_da_lista(ouro_bloqueados)
        alvo_anterior = estado.get("item_alvo")
        if not alvo_anterior or alvo_anterior.get("nome") != melhor["nome"]:
            log.info(
                f"  Alvo equipamento (acumulando gold): {melhor['nome']} @ {melhor['gold_necessario']}g líquido "
                f"({melhor['gold_bruto']}g bruto, req_skill={melhor.get('req_skill_valor',0)}, cat={melhor['categoria']})"
            )
    else:
        if estado.get("item_alvo"):
            log.info("  Alvo de equipamento: nenhum disponível (já usa o melhor) — limpando")
            del estado["item_alvo"]
            salvar_estado(estado)
        melhor = None

    if melhor:
        estado["item_alvo"] = melhor

    # Remove item_proximo legado se existir
    if estado.get("item_proximo"):
        del estado["item_proximo"]

    salvar_estado(estado)
    publicar_dashboard_equipamento(estado)


def publicar_dashboard_equipamento(estado):
    """Publica todos os alvos de compra no ciclo_file para o dashboard do launcher."""
    atualizar_ciclo_file("equipamento", {
        "item_alvo":    estado.get("item_alvo"),
        "pedra_alvo":   estado.get("pedra_alvo"),
        "anel_alvo":    estado.get("anel_alvo"),
        "amuleto_alvo": estado.get("amuleto_alvo"),
    })


def vender_item_atual(client, url_venda):
    """
    Vende 1 unidade do item atual na loja.
    url_venda: path relativo como /shop/sell/?waren=waffen&waffenid=X&wid=Y
    Retorna gold_recebido (int) ou 0 em caso de erro.
    """
    try:
        soup = client.get(url_venda, fragment=False)
    except Exception as e:
        log.warning(f"  Vender item: erro ao carregar página — {e}")
        return 0

    form = soup.find("form")
    if not form:
        log.warning("  Vender item: formulário não encontrado")
        return 0

    campos = {}
    for inp in form.find_all("input"):
        n = inp.get("name")
        if n:
            campos[n] = inp.get("value", "")
    campos["sell"] = "1"
    campos["amount"] = "1"

    costs_el = form.find("input", {"name": "costs"})
    gold_recebido = 0
    if costs_el:
        try:
            gold_recebido = int(costs_el.get("value", "0").replace(".", "").replace(",", ""))
        except ValueError:
            pass

    action = form.get("action") or "/"
    if action.startswith("http"):
        from urllib.parse import urlparse as _up
        action = _up(action).path
    if not action or action == "#":
        action = "/"

    try:
        client.post(action, data=campos, fragment=False)
        log.info(f"  ✓ Item vendido — recebeu {gold_recebido}g")
        return gold_recebido
    except Exception as e:
        log.warning(f"  Vender item: erro no POST — {e}")
        return 0


def _esta_bloqueado_por_missao(soup):
    """Retorna True se a página está bloqueada por missão ativa (não mostra loja)."""
    txt = soup.get_text(" ", strip=True).lower()
    return any(k in txt for k in [
        "out on an assignment", "still out on", "ainda em serviço",
        "canceljob", "auf einem auftrag", "en una misi",
    ])


def tentar_comprar_item_alvo(client, estado):
    """
    Compra o item_alvo se gold suficiente (gold_necessario = gold_bruto - gold_venda_atual).
    Se gold_atual < gold_bruto mas >= gold_necessario, vende item atual primeiro.
    Funciona para waffen, schilde e ruestungen. Retorna True se comprou algo.
    """
    if not COMPRAR_EQUIPAMENTO:
        return False
    alvo = estado.get("item_alvo")
    if not alvo:
        return False

    # Descarta item_alvo com preço inválido (< 50g = erro de parsing de versão anterior)
    gold_bruto_salvo = alvo.get("gold_bruto", alvo.get("gold_necessario", 0))
    if 0 < gold_bruto_salvo < 50:
        log.warning(f"  item_alvo '{alvo['nome']}' com preço inválido ({gold_bruto_salvo}g) — descartando e re-escaneando")
        del estado["item_alvo"]
        salvar_estado(estado)
        try:
            verificar_alvo_equipamento(client, estado)
        except Exception:
            pass
        return False

    gold_atual = estado.get("gold_atual", 0)
    if gold_atual < alvo["gold_necessario"]:
        return False

    gold_bruto = alvo.get("gold_bruto", alvo["gold_necessario"])
    url_venda_atual = alvo.get("url_venda_atual")

    # Se item era gold-bloqueado (url_compra=None), re-escaneia loja ANTES de vender
    # qualquer coisa — evita vender item atual sem conseguir comprar o novo
    url_compra = alvo.get("url_compra")
    if not url_compra:
        categoria = alvo.get("categoria", "waffen")
        log.info(f"  {alvo['nome']}: sem url_compra, re-escaneando /{categoria}/...")
        try:
            soup_loja = client.get(f"/shop/{categoria}/", fragment=False)
            todos_loja = _parsear_shop_todos_itens(soup_loja, categoria)
            req_alvo  = alvo.get("req_skill_valor", 0)
            gold_alvo = alvo.get("gold_bruto", alvo.get("gold_necessario", 0))
            # Match por req_skill_valor + gold (robusto entre idiomas; nome varia por servidor)
            match = next(
                (i for i in todos_loja
                 if i.get("req_skill_valor") == req_alvo
                 and abs(i["gold"] - gold_alvo) <= 50
                 and i.get("url_compra")),
                None
            )
            if not match:
                # Fallback: nome (funciona quando catálogo e servidor têm o mesmo idioma)
                match = next((i for i in todos_loja if i["nome"] == alvo["nome"] and i.get("url_compra")), None)
            if match:
                url_compra = match["url_compra"]
                alvo["url_compra"] = url_compra
                estado["item_alvo"] = alvo
                salvar_estado(estado)
                log.info(f"  Buy link obtido: {url_compra}")
            else:
                log.info(f"  {alvo['nome']}: ainda sem buy link na loja — aguardando")
                return False
        except Exception as e:
            log.warning(f"  Re-scan /{categoria}/ para '{alvo['nome']}': erro — {e}")
            return False

    log.info(f"  💰 Gold ({gold_atual}g) >= alvo {alvo['nome']} (liquido {alvo['gold_necessario']}g, bruto {gold_bruto}g) — comprando!")

    # Vende item atual APÓS confirmar que buy link existe
    if url_venda_atual:
        log.info(f"  Vendendo item atual antes da troca ({gold_bruto}g necessário, gold atual {gold_atual}g)...")
        gold_recebido = vender_item_atual(client, url_venda_atual)
        if gold_recebido > 0:
            gold_atual += gold_recebido
            log.info(f"  Venda ok: +{gold_recebido}g → total {gold_atual}g")
        elif gold_atual < gold_bruto:
            # Venda falhou E não tem gold suficiente sem ela
            log.warning(f"  Venda falhou e gold insuficiente ({gold_atual}g < {gold_bruto}g) — abortando compra de {alvo['nome']}")
            return False
        # Se venda falhou mas já tinha gold suficiente, continua mesmo assim
    if gold_atual < gold_bruto:
        log.warning(f"  Gold insuficiente após venda ({gold_atual}g < {gold_bruto}g) — abortando")
        return False

    # Carrega página da URL de compra (funciona para waffen, schilde e ruestungen)
    try:
        soup = client.get(url_compra, fragment=False)
    except Exception as e:
        log.warning(f"  Comprar {alvo['nome']}: erro ao carregar — {e}")
        return False

    if _esta_bloqueado_por_missao(soup):
        log.debug(f"  Comprar {alvo['nome']}: bloqueado por missão ativa")
        return False

    form = soup.find("form")
    if not form:
        trecho = soup.get_text(" ", strip=True)[:200]
        log.warning(f"  Comprar {alvo['nome']}: formulário não encontrado — limpando alvo para re-scan. Página: {trecho!r}")
        estado.pop("item_alvo", None)
        salvar_estado(estado)
        # Força re-scan na próxima iteração
        try:
            verificar_alvo_equipamento(client, estado)
        except Exception:
            pass
        return False

    campos = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        val  = inp.get("value", "")
        if name:
            campos[name] = val
    if "buy" not in campos:
        campos["buy"] = "1"

    # Garante amount=1 para todos os itens via item_alvo
    campos["amount"] = "1"

    action = form.get("action") or url_compra
    if action.startswith("http"):
        from urllib.parse import urlparse
        action = urlparse(action).path
    if not action or action == "#":
        action = url_compra

    try:
        client.post(action, data=campos, fragment=False)
    except Exception as e:
        log.warning(f"  Comprar {alvo['nome']}: erro no POST — {e}")
        return False

    log.info(f"  ✓ Comprou {alvo['nome']} (bruto {gold_bruto}g, liquido {alvo['gold_necessario']}g)")
    estado.pop("item_alvo", None)
    salvar_estado(estado)
    # Equipa o item recém comprado (waffen, schilde, ruestungen)
    try:
        equipar_melhor_item(client)
    except Exception as e:
        log.warning(f"  Auto-equipar pós-compra: erro — {e}")
    # Re-escaneia lojas para definir próximo alvo
    verificar_alvo_equipamento(client, estado)
    return True


def parsear_ferreiro(client):
    """
    Lê a página do ferreiro (/upgrade/) para a arma no inventário.
    Conta engastes: <a class="tooltip"> em .weapon-sockel — com imagem de pedra = preenchido.
    Retorna dict ou None.
    """
    try:
        r = client.session.get(BASE_URL + "/status/", timeout=15)
        soup_s = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.warning(f"  Ferreiro: erro ao ler /status/ — {e}")
        return None

    # Busca link /upgrade/ na seção de inventário
    upgrade_href = None
    for boxtop in soup_s.find_all("div", class_="box-top"):
        if "inventory" in boxtop.get_text().lower() or "inventar" in boxtop.get_text().lower():
            boxbg = boxtop.find_next_sibling("div", class_="box-bg")
            if boxbg:
                for a in boxbg.find_all("a", href=True):
                    if "/upgrade/" in a["href"]:
                        upgrade_href = a["href"]
                        break
            break

    if not upgrade_href:
        return None

    if upgrade_href.startswith("http"):
        from urllib.parse import urlparse, parse_qs as _pqs
        _p = urlparse(upgrade_href)
        upgrade_href = _p.path + ("?" + _p.query if _p.query else "")

    try:
        soup_f = client.get(upgrade_href, fragment=False)
    except Exception as e:
        log.warning(f"  Ferreiro: erro ao carregar página — {e}")
        return None

    sockel_td = soup_f.find("td", class_="weapon-sockel")
    if not sockel_td:
        return None

    todos_slots = sockel_td.find_all("a", class_="tooltip")
    engastes_total = len(todos_slots)

    # Imagens de pedras conhecidas
    _pedras_imgs = {"eis_s", "feuer_s", "drachen_s", "schatten_s", "heilig_s"}
    preenchidos = sum(
        1 for a in todos_slots
        if a.find("img") and any(p in a.find("img").get("src", "") for p in _pedras_imgs)
    )
    vazios = engastes_total - preenchidos

    from urllib.parse import parse_qs, urlparse
    params = parse_qs(urlparse(upgrade_href).query)
    wid      = int(params.get("wid",      [0])[0])
    waffenid = int(params.get("waffenid", [0])[0])

    return {
        "url_ferreiro":       upgrade_href,
        "wid":                wid,
        "waffenid":           waffenid,
        "engastes_total":     engastes_total,
        "engastes_preenchidos": preenchidos,
        "engastes_vazios":    vazios,
    }


def _parsear_pedra_bloqueada(soup):
    """
    Encontra a melhor pedra de alma com preço em gold mesmo sem botão de compra
    (quando o personagem não tem gold suficiente, o jogo oculta o botão Buy).
    Retorna dict compatível com _parsear_shop_listagem mas com url_compra=None.
    """
    melhor = None
    for tr in soup.find_all("tr"):
        # Deve ser uma linha de item (não de cabeçalho/rodapé)
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        # Não pode ter buy link (seria pego por _parsear_shop_listagem)
        if tr.find("a", href=lambda h: h and "wac=buy" in h):
            continue
        # Precisa ter goldstueck (preço em gold)
        gold_img = tr.find("img", src=lambda s: s and "goldstueck.gif" in s)
        if not gold_img:
            continue
        # Não pode ter preço em gema
        if tr.find("img", src=lambda s: s and ("edelstein.gif" in s or "coin.png" in s)):
            continue

        # Extrai preço — busca número antes do goldstueck
        preco = 0
        for span in tr.find_all("span"):
            if not span.find("img", src=lambda s: s and "goldstueck.gif" in s):
                continue
            m = re.search(r"[\d.,]+", span.get_text())
            if m:
                preco = int(m.group().replace(".", "").replace(",", ""))
                break
        if preco == 0:
            # fallback: número mais próximo do goldstueck no texto do tr
            txt_tr = tr.get_text(" ")
            m = re.search(r"(\d[\d.,]*)\s*$", txt_tr.split("goldstueck")[0] if "goldstueck" in txt_tr else "")
            if m:
                preco = int(m.group(1).replace(".", "").replace(",", ""))
        if preco <= 0:
            continue

        # Nome
        nome = "Item"
        strong = tr.find("strong") or tr.find("b")
        if strong:
            nome = strong.get_text(strip=True)[:80]
        else:
            for td in tds[1:]:
                t = td.get_text(strip=True)
                if t and len(t) > 3:
                    nome = t[:80]
                    break

        if melhor is None or preco < melhor["gold_necessario"]:
            melhor = {"nome": nome, "gold_necessario": preco, "url_compra": None, "categoria": "steine"}

    return melhor


def verificar_alvo_pedra(client, estado):
    """
    Verifica engastes vazios na arma e determina pedras a comprar.
    - Conta engastes vazios via ferreiro
    - Desconta pedras já no inventário
    - Busca melhor pedra por gold em /shop/steine/ (sem gemas, com buy button)
    - Fallback: pedra bloqueada por gold insuficiente (sem buy button ainda)
    - Salva em estado["pedra_alvo"]
    """
    if not COMPRAR_EQUIPAMENTO:
        return

    info = parsear_ferreiro(client)
    if not info or info["engastes_vazios"] == 0:
        if estado.get("pedra_alvo"):
            log.debug("  Pedra: sem engastes vazios — limpando alvo")
            del estado["pedra_alvo"]
            salvar_estado(estado)
        return

    engastes_vazios = info["engastes_vazios"]
    a_comprar = engastes_vazios  # pedras vão direto para a arma, não ficam no inventário

    # Melhor pedra disponível por gold (com buy button = já comprável)
    try:
        soup_shop = client.get("/shop/steine/", fragment=False)
    except Exception as e:
        log.warning(f"  Pedra: erro ao carregar loja — {e}")
        return

    melhor = _parsear_shop_listagem(soup_shop, "steine")
    if not melhor:
        # Fallback: pedra com preço gold mas sem buy link (gold insuficiente por ora)
        melhor = _parsear_pedra_bloqueada(soup_shop)
        if melhor:
            log.info(f"  Pedra: '{melhor['nome']}' @ {melhor['gold_necessario']}g ainda bloqueada (gold insuficiente) — salvando meta")
        else:
            log.debug("  Pedra: nenhuma pedra de alma com preço gold encontrada na loja")
            return

    gold_total = melhor["gold_necessario"] * a_comprar
    anterior   = estado.get("pedra_alvo", {})
    if anterior.get("nome") != melhor["nome"] or anterior.get("quantidade") != a_comprar:
        log.info(f"  💎 Alvo pedra: {a_comprar}x '{melhor['nome']}' @ {melhor['gold_necessario']}g = {gold_total}g total")

    estado["pedra_alvo"] = {
        "nome":            melhor["nome"],
        "gold_necessario": gold_total,
        "gold_unitario":   melhor["gold_necessario"],
        "url_compra":      melhor.get("url_compra"),  # pode ser None se ainda bloqueada
        "quantidade":      a_comprar,
        "wid":             info["wid"],
        "waffenid":        info["waffenid"],
    }
    salvar_estado(estado)
    publicar_dashboard_equipamento(estado)


def tentar_comprar_pedra(client, estado):
    """
    Compra pedras de alma quando gold suficiente.
    Fluxo: GET página de confirmação → POST (Continue) → POST se houver 2ª confirmação (Accept).
    Retorna True se comprou alguma.
    """
    if not COMPRAR_EQUIPAMENTO:
        return False
    alvo = estado.get("pedra_alvo")
    if not alvo:
        return False

    gold_atual = estado.get("gold_atual", 0)
    if gold_atual < alvo["gold_necessario"]:
        return False

    # Se url_compra ainda não disponível (estava bloqueada), re-busca na loja
    url_compra = alvo.get("url_compra")
    if not url_compra:
        try:
            soup_shop = client.get("/shop/steine/", fragment=False)
            atualizado = _parsear_shop_listagem(soup_shop, "steine")
            if atualizado and atualizado["nome"] == alvo["nome"]:
                url_compra = atualizado["url_compra"]
                alvo["url_compra"] = url_compra
                estado["pedra_alvo"] = alvo
                salvar_estado(estado)
                log.info(f"  Pedra: buy link obtido — {url_compra}")
            else:
                log.debug(f"  Pedra: buy link para '{alvo['nome']}' ainda não disponível (gold insuficiente?)")
                return False
        except Exception as e:
            log.warning(f"  Pedra: erro ao buscar buy link — {e}")
            return False

    log.info(f"  💰 Gold ({gold_atual}g) >= pedras {alvo['quantidade']}x '{alvo['nome']}' ({alvo['gold_necessario']}g) — comprando!")

    compradas = 0
    for i in range(alvo["quantidade"]):
        # Passo 1: GET página de confirmação (tem quantidade + preço + botão Continue)
        try:
            soup = client.get(url_compra, fragment=False)
        except Exception as e:
            log.warning(f"  Comprar pedra #{i+1}: erro ao carregar — {e}")
            break

        if _esta_bloqueado_por_missao(soup):
            log.debug(f"  Comprar pedra #{i+1}: bloqueado por missão ativa")
            break

        form = soup.find("form")
        if not form:
            log.warning(f"  Comprar pedra #{i+1}: formulário não encontrado")
            break

        campos = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            val  = inp.get("value", "")
            if name:
                campos[name] = val
        if "amount" in campos:
            campos["amount"] = "1"
        if "buy" not in campos:
            campos["buy"] = "1"

        action = form.get("action") or url_compra
        if action.startswith("http"):
            from urllib.parse import urlparse
            action = urlparse(action).path
        if not action or action == "#":
            action = url_compra

        # Passo 2: POST (Continue) — pode retornar 2ª confirmação (Accept)
        try:
            soup2 = client.post(action, data=campos, fragment=False)
        except Exception as e:
            log.warning(f"  Comprar pedra #{i+1}: erro no POST — {e}")
            break

        # Verifica se há 2ª confirmação (Accept) — mesma estrutura da armadura
        if soup2:
            form2 = soup2.find("form") if hasattr(soup2, "find") else None
            if form2:
                campos2 = {inp.get("name"): inp.get("value","") for inp in form2.find_all("input") if inp.get("name")}
                action2 = form2.get("action") or action
                if action2.startswith("http"):
                    from urllib.parse import urlparse as _up
                    action2 = _up(action2).path
                # Só faz 2º POST se parece um form de confirmação (tem ac=shop ou buy)
                if campos2.get("ac") == "shop" or "buy" in campos2:
                    try:
                        client.post(action2, data=campos2, fragment=False)
                        log.debug(f"  Comprar pedra #{i+1}: 2ª confirmação submetida")
                    except Exception as e:
                        log.warning(f"  Comprar pedra #{i+1}: erro no 2º POST — {e}")
                        break

        compradas += 1

    if compradas > 0:
        log.info(f"  ✓ Comprou {compradas}x '{alvo['nome']}'")
        estado.pop("pedra_alvo", None)
        salvar_estado(estado)
        # Tenta inserir imediatamente
        inserir_pedra_na_arma(client, alvo)
        verificar_alvo_pedra(client, estado)
        return True

    return False


def inserir_pedra_na_arma(client, alvo):
    """
    Insere pedra de alma na arma após compra.
    Carrega página do ferreiro, encontra seção Inventário,
    e executa a ação de inserção (form POST ou link GET).
    Retorna True se inseriu com sucesso.
    """
    wid      = alvo.get("wid")
    waffenid = alvo.get("waffenid")
    if not wid or not waffenid:
        log.warning("  Inserir pedra: wid/waffenid ausente no alvo")
        return False

    url_ferreiro = f"/upgrade/?waffenid={waffenid}&wid={wid}"

    # Pode precisar de até N tentativas (uma por engaste vazio)
    for tentativa in range(1, 5):
        try:
            soup = client.get(url_ferreiro, fragment=False)
        except Exception as e:
            log.warning(f"  Inserir pedra: erro ao carregar ferreiro — {e}")
            return False

        # Encontra seção Inventário na página do ferreiro
        inv_bg = None
        for boxtop in soup.find_all("div", class_="box-top"):
            if "invent" in boxtop.get_text().lower():
                inv_bg = boxtop.find_next_sibling("div", class_="box-bg")
                break

        if not inv_bg:
            log.debug("  Inserir pedra: seção inventário não encontrada na página do ferreiro")
            return False

        txt_inv = inv_bg.get_text(strip=True)
        if any(k in txt_inv.lower() for k in ["nenhum", "keine", "no item", "kein"]):
            log.debug(f"  Inserir pedra: nenhuma pedra no inventário do ferreiro (tentativa {tentativa})")
            return False

        # Caso 1: Formulário na seção inventário
        form = inv_bg.find("form")
        if form:
            campos = {}
            for inp in form.find_all("input"):
                n = inp.get("name")
                v = inp.get("value", "")
                if n:
                    campos[n] = v
            # Também trata radio buttons — seleciona o primeiro disponível
            for radio in form.find_all("input", type="radio"):
                n = radio.get("name")
                v = radio.get("value", "")
                if n and n not in campos:
                    campos[n] = v
            action = form.get("action") or url_ferreiro
            if action.startswith("http"):
                from urllib.parse import urlparse as _up
                _parsed = _up(action)
                action = _parsed.path + ("?" + _parsed.query if _parsed.query else "")
            try:
                client.post(action, data=campos, fragment=False)
                log.info(f"  💎 Pedra inserida na arma (tentativa {tentativa})")
                continue  # verifica se há mais engastes vazios
            except Exception as e:
                log.warning(f"  Inserir pedra: erro no POST — {e}")
                return False

        # Caso 2: Link de inserção na seção inventário
        _insert_kw = ["setstone", "einsetzen", "insert", "wac=set", "sockel", "stein"]
        link = inv_bg.find("a", href=lambda h: h and any(k in h.lower() for k in _insert_kw))
        if not link:
            # qualquer link que não seja navegação principal
            link = inv_bg.find("a", href=lambda h: h and "/upgrade/" in h)
        if link:
            href = link["href"]
            if href.startswith("http"):
                from urllib.parse import urlparse as _up
                _p = _up(href)
                href = _p.path + ("?" + _p.query if _p.query else "")
            try:
                client.get(href, fragment=False)
                log.info(f"  💎 Pedra inserida na arma via link (tentativa {tentativa})")
                continue
            except Exception as e:
                log.warning(f"  Inserir pedra: erro no GET link — {e}")
                return False

        log.warning(f"  Inserir pedra: mecanismo de inserção não reconhecido. HTML: {inv_bg.prettify()[:400]}")
        return False

    return True


def verificar_alvo_anel(client, estado):
    """
    Determina aneis a comprar. Máximo 2 simultâneos.
    Lógica de upgrade por level:
      - level_equipado >= level_loja → ignora (atual já é melhor ou igual)
      - level_equipado < level_loja E level_loja <= level_personagem → compra/acumula gold
    """
    if not COMPRAR_EQUIPAMENTO:
        return

    MAX_ANEIS = 2
    try:
        soup = client.get("/shop/ringe/", fragment=False)
    except Exception as e:
        log.warning(f"  Anel: erro ao carregar loja — {e}")
        return

    player_level = MY_STATS.get("level", estado.get("level", 0))

    # Localiza seção inventário para contagem real e levels equipados
    total_aneis = 0
    levels_equipados = []
    inv_boxbg = None
    for _boxtop in soup.find_all("div", class_="box-top"):
        if "invent" in _boxtop.get_text().strip().lower():
            inv_boxbg = _boxtop.find_next_sibling("div", class_="box-bg")
            break
    sell_info_equipados = []  # {"level": lv, "sell_url": url, "sell_val": val}
    if inv_boxbg:
        for tr in inv_boxbg.find_all("tr", class_="mobile-cols-2"):
            _tr_txt = tr.get_text(separator=" ", strip=True)
            _m_qty = re.search(r"(\d+)\s+item", _tr_txt, re.IGNORECASE)
            qty = int(_m_qty.group(1)) if _m_qty else 1
            total_aneis += qty
            if re.search(r"equipped|equipado|ausger[üu]stet", _tr_txt, re.IGNORECASE):
                _m_lv = re.search(r"(?:level|n[íi]vel|stufe)\s*[:\-]?\s*(\d+)", _tr_txt, re.IGNORECASE)
                lv = int(_m_lv.group(1)) if _m_lv else 0
                levels_equipados.extend([lv] * min(qty, MAX_ANEIS))
                sell_a = tr.find("a", href=lambda h: h and "/shop/sell/" in h)
                sell_url = sell_a["href"] if sell_a else None
                m_sv = re.search(r"(?:item\s+value|itemwert|warenwert|valor\s+(?:do\s+)?item)[:\s]+(\d[\d.,]*)", _tr_txt, re.IGNORECASE)
                sell_val = int(m_sv.group(1).replace(".", "").replace(",", "")) if m_sv else 0
                sell_info_equipados.append({"level": lv, "sell_url": sell_url, "sell_val": sell_val})

    log.debug(f"  Anel: {total_aneis} total (equipados+bolsa), levels equipados: {levels_equipados}, sell_info: {sell_info_equipados}, player_lv={player_level}")

    a_comprar = max(0, MAX_ANEIS - total_aneis)
    pior_level_eq = min(levels_equipados) if levels_equipados else -1

    # Varre shop listing (pula TRs da seção inventário para não misturar dados)
    inv_tr_ids = set(id(tr) for tr in (inv_boxbg.find_all("tr") if inv_boxbg else []))
    candidatos = []    # tem level certo + buy link (pode comprar agora)
    ouro_bloq  = []    # tem level certo mas sem buy link (acumular gold)

    for tr in soup.find_all("tr", class_="mobile-cols-2"):
        if id(tr) in inv_tr_ids:
            continue
        # Pula itens cujo preço é gema
        if tr.find("img", src=lambda s: s and ("edelstein.gif" in s or "coin.png" in s)):
            continue
        # Precisa ter preço em gold
        if not tr.find("img", src=lambda s: s and "goldstueck.gif" in s):
            continue
        # Pula itens já equipados (sell link na listagem)
        if tr.find("a", href=lambda h: h and "/shop/sell/" in h):
            continue

        tr_txt = tr.get_text(separator=" ", strip=True)
        m_lv = re.search(r"(?:level|n[íi]vel|stufe)\s*[:\-]?\s*(\d+)", tr_txt, re.IGNORECASE)
        req_lv = int(m_lv.group(1)) if m_lv else 0

        # Modo upgrade (slots cheios): só anéis melhores que o pior equipado
        # Modo compra (slot vazio): qualquer anel que o personagem possa usar
        if a_comprar == 0 and req_lv <= pior_level_eq:
            continue
        # Personagem deve poder usar: req_level <= player_level (0 = sem requisito)
        if req_lv > 0 and req_lv > player_level:
            continue

        # Extrai preço (maior valor entre spans com goldstueck)
        gold = 0
        for span in tr.find_all("span"):
            if not span.find("img", src=lambda s: s and "goldstueck.gif" in s):
                continue
            m = re.search(r"[\d.,]+", span.get_text())
            if m:
                gold = max(gold, int(m.group().replace(".", "").replace(",", "")))
        if gold == 0:
            m = re.search(r"\b(\d[\d.,]+)\b", tr_txt)
            if m:
                gold = int(m.group(1).replace(".", "").replace(",", ""))
        if 0 < gold < 50:
            continue  # preço inválido

        nome = "Anel"
        strong = tr.find("strong") or tr.find("b")
        if strong:
            nome = strong.get_text(strip=True)[:80]

        buy_a = tr.find("a", href=lambda h: h and "wac=buy" in h)
        url_compra = None
        if buy_a:
            href = buy_a["href"]
            if href.startswith("http"):
                from urllib.parse import urlparse as _up
                _p = _up(href)
                url_compra = _p.path + ("?" + _p.query if _p.query else "")
            else:
                url_compra = href

        item = {"nome": nome, "gold_necessario": gold, "req_level": req_lv,
                "url_compra": url_compra, "categoria": "ringe"}
        if buy_a:
            candidatos.append(item)
        else:
            ouro_bloq.append(item)

    lista = candidatos or ouro_bloq
    if not lista:
        if estado.get("anel_alvo"):
            log.debug("  Anel: nenhum upgrade disponível — limpando alvo")
            del estado["anel_alvo"]
            salvar_estado(estado)
        return

    # Melhor: maior req_level (mais poderoso), desempate por maior gold
    melhor = max(lista, key=lambda x: (x.get("req_level", 0), x.get("gold_necessario", 0)))
    gold_bruto = melhor["gold_necessario"]

    # Se slots cheios, vender pior anel para abrir espaço
    vender_pior = None
    if a_comprar == 0:
        if sell_info_equipados:
            pior = min(sell_info_equipados, key=lambda x: x["level"])
            if pior.get("sell_url"):
                vender_pior = pior
        a_comprar = 1

    gold_venda_pior = vender_pior["sell_val"] if vender_pior else 0
    gold_necessario = max(0, gold_bruto - gold_venda_pior) * a_comprar

    anterior = estado.get("anel_alvo", {})
    if anterior.get("nome") != melhor["nome"] or anterior.get("quantidade") != a_comprar:
        log.info(
            f"  💍 Alvo anel: {a_comprar}x '{melhor['nome']}' @ {gold_bruto}g/un "
            f"(vende pior={gold_venda_pior}g → custo líq={gold_necessario}g) "
            f"(req_lv {melhor.get('req_level',0)}, pior_eq={pior_level_eq}, player_lv={player_level})"
        )

    estado["anel_alvo"] = {
        "nome":            melhor["nome"],
        "gold_necessario": gold_necessario,
        "gold_unitario":   gold_bruto,
        "url_compra":      melhor["url_compra"],
        "quantidade":      a_comprar,
    }
    if vender_pior:
        estado["anel_alvo"]["url_venda_pior"] = vender_pior["sell_url"]
        estado["anel_alvo"]["gold_venda_pior"] = vender_pior["sell_val"]
    salvar_estado(estado)
    publicar_dashboard_equipamento(estado)


def tentar_comprar_anel(client, estado):
    """
    Compra aneis quando gold suficiente (até 2).
    Compra um por vez em loop.
    Retorna True se comprou algum.
    """
    if not COMPRAR_EQUIPAMENTO:
        return False
    alvo = estado.get("anel_alvo")
    if not alvo:
        return False

    gold_atual = estado.get("gold_atual", 0)
    if gold_atual < alvo["gold_necessario"]:
        return False

    if not alvo.get("url_compra"):
        log.debug(f"  Anel: '{alvo.get('nome')}' sem url_compra — acumulando gold")
        return False

    log.info(f"  💰 Gold ({gold_atual}g) >= aneis {alvo['quantidade']}x '{alvo['nome']}' ({alvo['gold_necessario']}g) — comprando!")

    # Vende pior anel se necessário para abrir slot
    if alvo.get("url_venda_pior"):
        try:
            gold_recebido = vender_item_atual(client, alvo["url_venda_pior"])
            log.info(f"  Anel: vendeu pior anel equipado — recebeu {gold_recebido}g")
        except Exception as e:
            log.warning(f"  Anel: erro ao vender pior anel — {e}")

    comprados = 0
    for i in range(alvo["quantidade"]):
        try:
            soup = client.get(alvo["url_compra"], fragment=False)
        except Exception as e:
            log.warning(f"  Comprar anel #{i+1}: erro ao carregar — {e}")
            break

        if _esta_bloqueado_por_missao(soup):
            log.debug(f"  Comprar anel #{i+1}: bloqueado por missão ativa")
            break

        form = soup.find("form")
        if not form:
            trecho = soup.get_text(" ", strip=True)[:200]
            log.warning(f"  Comprar anel #{i+1}: formulário não encontrado — limpando alvo. Página: {trecho!r}")
            estado.pop("anel_alvo", None)
            salvar_estado(estado)
            try:
                verificar_alvo_anel(client, estado)
            except Exception:
                pass
            break

        campos = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            val  = inp.get("value", "")
            if name:
                campos[name] = val
        if "buy" not in campos:
            campos["buy"] = "1"

        action = form.get("action") or alvo["url_compra"]
        if action.startswith("http"):
            from urllib.parse import urlparse
            action = urlparse(action).path
        if not action or action == "#":
            action = alvo["url_compra"]

        try:
            client.post(action, data=campos, fragment=False)
            comprados += 1
        except Exception as e:
            log.warning(f"  Comprar anel #{i+1}: erro no POST — {e}")
            break

    if comprados > 0:
        log.info(f"  ✓ Comprou {comprados}x '{alvo['nome']}'")
        estado.pop("anel_alvo", None)
        salvar_estado(estado)
        verificar_alvo_anel(client, estado)
        try:
            equipar_melhor_item(client)
        except Exception as e:
            log.warning(f"  Auto-equipar anel: erro — {e}")
        return True

    return False


def verificar_alvo_amuleto(client, estado):
    """
    Determina amuleto a comprar. Máximo 1.
    Lógica de upgrade por level:
      - level_equipado >= level_loja → ignora (atual já é melhor ou igual)
      - level_equipado < level_loja E level_loja <= level_personagem → compra/acumula gold
    Se já tem 1 amuleto mas não é upgrade, não compra.
    """
    if not COMPRAR_EQUIPAMENTO:
        return

    try:
        soup = client.get("/shop/amulette/", fragment=False)
    except Exception as e:
        log.warning(f"  Amuleto: erro ao carregar loja — {e}")
        return

    player_level = MY_STATS.get("level", estado.get("level", 0))

    # Localiza seção inventário para contagem e level do equipado
    total_amuletos = 0
    level_amuleto_eq = -1
    sell_url_amuleto_eq = None
    sell_val_amuleto_eq = 0
    inv_boxbg = None
    for _boxtop in soup.find_all("div", class_="box-top"):
        if "invent" in _boxtop.get_text().strip().lower():
            inv_boxbg = _boxtop.find_next_sibling("div", class_="box-bg")
            break
    if inv_boxbg:
        for tr in inv_boxbg.find_all("tr", class_="mobile-cols-2"):
            _tr_txt = tr.get_text(separator=" ", strip=True)
            _m_qty = re.search(r"(\d+)\s+item", _tr_txt, re.IGNORECASE)
            qty = int(_m_qty.group(1)) if _m_qty else 1
            total_amuletos += qty
            if re.search(r"equipped|equipado|ausger[üu]stet", _tr_txt, re.IGNORECASE):
                _m_lv = re.search(r"(?:level|n[íi]vel|stufe)\s*[:\-]?\s*(\d+)", _tr_txt, re.IGNORECASE)
                level_amuleto_eq = int(_m_lv.group(1)) if _m_lv else 0
                sell_a = tr.find("a", href=lambda h: h and "/shop/sell/" in h)
                sell_url_amuleto_eq = sell_a["href"] if sell_a else None
                m_sv = re.search(r"(?:item\s+value|itemwert|warenwert|valor\s+(?:do\s+)?item)[:\s]+(\d[\d.,]*)", _tr_txt, re.IGNORECASE)
                sell_val_amuleto_eq = int(m_sv.group(1).replace(".", "").replace(",", "")) if m_sv else 0

    log.debug(f"  Amuleto: {total_amuletos} total, level_eq={level_amuleto_eq}, sell_url={sell_url_amuleto_eq}, sell_val={sell_val_amuleto_eq}, player_lv={player_level}")

    # Varre shop listing (pula TRs da seção inventário)
    inv_tr_ids = set(id(tr) for tr in (inv_boxbg.find_all("tr") if inv_boxbg else []))
    candidatos = []
    ouro_bloq  = []

    for tr in soup.find_all("tr", class_="mobile-cols-2"):
        if id(tr) in inv_tr_ids:
            continue
        if tr.find("img", src=lambda s: s and ("edelstein.gif" in s or "coin.png" in s)):
            continue
        if not tr.find("img", src=lambda s: s and "goldstueck.gif" in s):
            continue
        if tr.find("a", href=lambda h: h and "/shop/sell/" in h):
            continue

        tr_txt = tr.get_text(separator=" ", strip=True)
        m_lv = re.search(r"(?:level|n[íi]vel|stufe)\s*[:\-]?\s*(\d+)", tr_txt, re.IGNORECASE)
        req_lv = int(m_lv.group(1)) if m_lv else 0

        # Deve ser upgrade: req_level > level do equipado atual
        if req_lv <= level_amuleto_eq:
            continue
        # Personagem deve poder usar: req_level <= player_level
        if req_lv > 0 and req_lv > player_level:
            continue

        gold = 0
        for span in tr.find_all("span"):
            if not span.find("img", src=lambda s: s and "goldstueck.gif" in s):
                continue
            m = re.search(r"[\d.,]+", span.get_text())
            if m:
                gold = max(gold, int(m.group().replace(".", "").replace(",", "")))
        if gold == 0:
            m = re.search(r"\b(\d[\d.,]+)\b", tr_txt)
            if m:
                gold = int(m.group(1).replace(".", "").replace(",", ""))
        if 0 < gold < 50:
            continue

        nome = "Amuleto"
        strong = tr.find("strong") or tr.find("b")
        if strong:
            nome = strong.get_text(strip=True)[:80]

        buy_a = tr.find("a", href=lambda h: h and "wac=buy" in h)
        url_compra = None
        if buy_a:
            href = buy_a["href"]
            if href.startswith("http"):
                from urllib.parse import urlparse as _up
                _p = _up(href)
                url_compra = _p.path + ("?" + _p.query if _p.query else "")
            else:
                url_compra = href

        item = {"nome": nome, "gold_necessario": gold, "req_level": req_lv,
                "url_compra": url_compra, "categoria": "amulette"}
        if buy_a:
            candidatos.append(item)
        else:
            ouro_bloq.append(item)

    lista = candidatos or ouro_bloq
    if not lista:
        if estado.get("amuleto_alvo"):
            log.debug("  Amuleto: nenhum upgrade disponível — limpando alvo")
            del estado["amuleto_alvo"]
            salvar_estado(estado)
        return

    melhor = max(lista, key=lambda x: (x.get("req_level", 0), x.get("gold_necessario", 0)))
    gold_bruto_amu = melhor["gold_necessario"]
    gold_necessario_amu = max(0, gold_bruto_amu - sell_val_amuleto_eq)
    anterior = estado.get("amuleto_alvo", {})
    if anterior.get("nome") != melhor["nome"]:
        log.info(
            f"  📿 Alvo amuleto: '{melhor['nome']}' @ {gold_bruto_amu}g "
            f"(vende atual={sell_val_amuleto_eq}g → custo líq={gold_necessario_amu}g) "
            f"(req_lv {melhor.get('req_level',0)}, eq_lv={level_amuleto_eq}, player_lv={player_level})"
        )

    estado["amuleto_alvo"] = {
        "nome":            melhor["nome"],
        "gold_necessario": gold_necessario_amu,
        "gold_bruto":      gold_bruto_amu,
        "url_compra":      melhor["url_compra"],
    }
    if sell_url_amuleto_eq:
        estado["amuleto_alvo"]["url_venda_atual"] = sell_url_amuleto_eq
        estado["amuleto_alvo"]["gold_venda_atual"] = sell_val_amuleto_eq
    salvar_estado(estado)
    publicar_dashboard_equipamento(estado)


def tentar_comprar_amuleto(client, estado):
    """
    Compra amuleto quando gold suficiente.
    Retorna True se comprou.
    """
    if not COMPRAR_EQUIPAMENTO:
        return False
    alvo = estado.get("amuleto_alvo")
    if not alvo:
        return False

    gold_atual = estado.get("gold_atual", 0)
    if gold_atual < alvo["gold_necessario"]:
        return False

    if not alvo.get("url_compra"):
        log.debug(f"  Amuleto: '{alvo.get('nome')}' sem url_compra — acumulando gold")
        return False

    log.info(f"  💰 Gold ({gold_atual}g) >= amuleto '{alvo['nome']}' ({alvo['gold_necessario']}g) — comprando!")

    # Vende amuleto atual se necessário para liberar slot
    if alvo.get("url_venda_atual"):
        try:
            gold_recebido = vender_item_atual(client, alvo["url_venda_atual"])
            log.info(f"  Amuleto: vendeu amuleto atual — recebeu {gold_recebido}g")
        except Exception as e:
            log.warning(f"  Amuleto: erro ao vender amuleto atual — {e}")

    try:
        soup = client.get(alvo["url_compra"], fragment=False)
    except Exception as e:
        log.warning(f"  Comprar amuleto: erro ao carregar — {e}")
        return False

    if _esta_bloqueado_por_missao(soup):
        log.debug("  Comprar amuleto: bloqueado por missão ativa")
        return False

    form = soup.find("form")
    if not form:
        trecho = soup.get_text(" ", strip=True)[:200]
        log.warning(f"  Comprar amuleto: formulário não encontrado — limpando alvo. Página: {trecho!r}")
        estado.pop("amuleto_alvo", None)
        salvar_estado(estado)
        try:
            verificar_alvo_amuleto(client, estado)
        except Exception:
            pass
        return False

    campos = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        val  = inp.get("value", "")
        if name:
            campos[name] = val
    if "buy" not in campos:
        campos["buy"] = "1"

    action = form.get("action") or alvo["url_compra"]
    if action.startswith("http"):
        from urllib.parse import urlparse
        action = urlparse(action).path
    if not action or action == "#":
        action = alvo["url_compra"]

    try:
        client.post(action, data=campos, fragment=False)
    except Exception as e:
        log.warning(f"  Comprar amuleto: erro no POST — {e}")
        return False

    log.info(f"  ✓ Comprou '{alvo['nome']}' (gastou ~{alvo['gold_necessario']}g)")
    estado.pop("amuleto_alvo", None)
    salvar_estado(estado)
    verificar_alvo_amuleto(client, estado)
    equipar_melhor_item(client)
    return True


def equipar_melhor_item(client):
    """
    Visita /landsitz/ e equipa itens do inventário que sejam melhores que os
    atualmente equipados (comparação por requisito máximo de skill/level).
    Chamado após compra de arma, escudo, anel ou amuleto.
    """
    try:
        soup = client.get("/landsitz/", fragment=False)
    except Exception as e:
        log.warning(f"  Equipar: erro ao carregar /landsitz/ — {e}")
        return

    # Localiza seção inventário (EN: inventory, PT: inventário, DE: Inventar, ES: Inventario)
    inv_bg = None
    for boxtop in soup.find_all("div", class_="box-top"):
        t = boxtop.get_text().strip().lower()
        if "invent" in t:
            inv_bg = boxtop.find_next_sibling("div", class_="box-bg")
            break
    if not inv_bg:
        return

    def parse_tier(tr_elem):
        """Maior N em linhas de requisito do item (Condição/Condition/Requirement - X: N).
        Tenta primeiro no span com font-size; cai back no texto completo do TR."""
        span = tr_elem.find("span", style=lambda s: s and "font-size" in s)
        txt = span.get_text() if span else tr_elem.get_text(separator=" ", strip=True)
        nums = re.findall(
            r"(?:Condi[çc][ãa]o|Condition|Requirement|Voraussetzung)\s*-\s*[^:]+:\s*(\d+)",
            txt, re.IGNORECASE
        )
        return max((int(n) for n in nums), default=0)

    def slot_de_href(href, tr=None):
        if "wid=" in href and "uwid" not in href:  return "weapon"
        if "sid=" in href and "usid" not in href:  return "shield"
        if "rid=" in href:                          return "ring"
        if "aid=" in href:                          return "amulet"
        # Equip via iid= (id generico do inventario) — typ= indica o slot
        if "iid=" in href:
            m = re.search(r"[?&]typ=(\d+)", href)
            if m:
                typ = int(m.group(1))
                if typ == 1: return "shield"
                if typ == 2: return "weapon"
                if typ == 3: return "armor"
        # armid= é usado para armadura E aneis/amuletos em alguns servers KF
        # — usa texto do TR para distinguir
        if "armid=" in href:
            if tr is not None:
                _txt = tr.get_text(" ", strip=True).lower()
                if re.search(r"\banel\b|\bring\b|anillo|ringe?\b", _txt):
                    return "ring"
                if re.search(r"\bamuleto\b|\bamulet\b|amulette?\b", _txt):
                    return "amulet"
            return "armor"
        return None

    # Monta mapa nome → tier de todos os itens do inventário
    tier_map = {}
    for tr in inv_bg.find_all("tr", class_="mobile-cols-2"):
        strong = tr.find("strong")
        if strong:
            tier_map[strong.get_text(strip=True)] = parse_tier(tr)

    # Coleta itens equipados por slot via #equipped-items
    equipped_div = soup.find("div", id="equipped-items")
    tiers_equipados = {}  # slot → [tier, ...]
    if equipped_div:
        for span in equipped_div.find_all("span", attrs={"data-href": True}):
            data_href = span.get("data-href", "")
            data_tip  = span.get("data-tooltip", "")
            nome_m = re.search(r"<b[^>]*>([^<]+)</b>", data_tip)
            if not nome_m:
                continue
            nome_eq = BeautifulSoup(nome_m.group(1), "html.parser").get_text(strip=True)

            if   "uwid=" in data_href:  slot = "weapon"
            elif "usid=" in data_href:  slot = "shield"
            elif "armid=" in data_href: slot = "armor"
            elif "rid="   in data_href: slot = "ring"
            elif "aid="   in data_href: slot = "amulet"
            else:                       continue

            tiers_equipados.setdefault(slot, []).append(tier_map.get(nome_eq, 0))

    # Percorre itens não equipados com botão Equip
    for tr in inv_bg.find_all("tr", class_="mobile-cols-2"):
        tr_txt = tr.get_text()
        if re.search(r"equipped|equipado|ausger[üu]stet", tr_txt, re.IGNORECASE):
            continue

        equip_a = tr.find("a", href=lambda h: h and "/landsitz/?" in h
                           and "uwid" not in h and "usid" not in h and "wac" not in h)
        if not equip_a:
            continue

        strong = tr.find("strong")
        if not strong:
            continue
        nome = strong.get_text(strip=True)

        href = equip_a["href"]
        slot = slot_de_href(href, tr)
        if not slot:
            continue

        tier_novo = parse_tier(tr)
        tiers_eq  = tiers_equipados.get(slot, [])

        if slot == "ring":
            deve_equipar = len(tiers_eq) < 2 or tier_novo > (min(tiers_eq) if tiers_eq else -1)
        elif not tiers_eq:
            deve_equipar = True
        else:
            deve_equipar = tier_novo > min(tiers_eq)

        if not deve_equipar:
            log.debug(f"  Equipar: '{nome}' (tier {tier_novo}) não é melhor que {tiers_eq} — skip")
            continue

        from urllib.parse import urlparse as _up_eq
        if href.startswith("http"):
            _p_eq = _up_eq(href)
            url = _p_eq.path + ("?" + _p_eq.query if _p_eq.query else "")
        else:
            url = href
        log.info(f"  Equipando '{nome}' (tier {tier_novo} > {tiers_eq}, slot {slot})")
        try:
            client.get(url, fragment=False)
            log.info(f"  ✓ Equipado: '{nome}'")
            # Atualiza estado local para iterações seguintes (e.g. 2 aneis)
            tiers_equipados.setdefault(slot, []).append(tier_novo)
            if slot != "ring":
                tiers_equipados[slot] = [tier_novo]
        except Exception as e:
            log.warning(f"  Equipar '{nome}': erro — {e}")


def limpar_duplicatas_inventario(client):
    """
    Para cada loja, verifica duplicatas no inventário e vende os piores:
    - weapon, shield, armor, amulet: mantém 1 (maior tier)
    - ring: mantém 2 (2 maiores tier)
    Deve ser chamado APÓS equipar_melhor_item (que já equipou o melhor).
    """
    def _tier_tr(tr):
        span = tr.find("span", style=lambda s: s and "font-size" in s)
        if not span:
            return 0
        nums = re.findall(
            r"(?:Condi[çc][ãa]o|Condition|Requirement|Voraussetzung)\s*-\s*[^:]+:\s*(\d+)",
            span.get_text(), re.IGNORECASE
        )
        return max((int(n) for n in nums), default=0)

    lojas = [
        ("/shop/waffen/",     1),
        ("/shop/schilde/",    1),
        ("/shop/ruestungen/", 1),
        ("/shop/amulette/",   1),
        ("/shop/ringe/",      2),
    ]

    for url_loja, max_qty in lojas:
        try:
            soup = client.get(url_loja, fragment=False)
        except Exception as e:
            log.warning(f"  Limpar inventário {url_loja}: erro — {e}")
            continue

        if _esta_bloqueado_por_missao(soup):
            continue

        # Localiza seção inventário da loja
        inv_bg = None
        for boxtop in soup.find_all("div", class_="box-top"):
            if "invent" in boxtop.get_text().strip().lower():
                inv_bg = boxtop.find_next_sibling("div", class_="box-bg")
                break
        if not inv_bg:
            continue

        # Coleta todos os itens com sell link (inventário real do jogador)
        itens = []
        for tr in inv_bg.find_all("tr", class_="mobile-cols-2"):
            sell_a = tr.find("a", href=lambda h: h and "/shop/sell/" in h)
            if not sell_a:
                continue
            tr_txt = tr.get_text()
            is_eq = bool(re.search(r"equipped|equipado|ausger[üu]stet", tr_txt, re.IGNORECASE))
            tier = _tier_tr(tr)
            sell_href = sell_a["href"]
            if sell_href.startswith("http"):
                from urllib.parse import urlparse as _up_s
                _p_s = _up_s(sell_href)
                sell_href = _p_s.path + ("?" + _p_s.query if _p_s.query else "")
            m_qty = re.search(r"(\d+)\s+item", tr_txt, re.IGNORECASE)
            qty = int(m_qty.group(1)) if m_qty else 1
            itens.append({"tier": tier, "equipado": is_eq, "sell_url": sell_href, "qty": qty})

        total = sum(i["qty"] for i in itens)
        if total <= max_qty:
            continue

        # Ordena: maior tier primeiro; entre iguais, equipado tem prioridade (fica)
        itens.sort(key=lambda x: (-x["tier"], 0 if x["equipado"] else 1))

        kept = 0
        for item in itens:
            to_keep = min(item["qty"], max_qty - kept)
            to_sell = item["qty"] - to_keep
            kept += to_keep
            if to_sell <= 0:
                continue
            if item["equipado"]:
                # Nunca tenta vender item equipado (inconsistência de dados)
                log.debug(f"  Limpar: item equipado marcado para vender — ignorado")
                continue
            log.info(f"  Limpeza inventário {url_loja}: vendendo {to_sell}x tier={item['tier']}")
            for _ in range(to_sell):
                vender_item_atual(client, item["sell_url"])


def rotina_encerramento_noturno(client):
    """
    Executada quando o horário de operação termina:
    1. Se em taverna, espera terminar
    2. Compra toda armadura possível com o gold
    3. Entra na taverna em loop até horario_inicio
    """
    log.info(f"⏰ Horário de parada ({HORARIO_PARADA}) — iniciando encerramento noturno")

    # 1. Aguarda se já estiver em taverna
    em_tav, seg_tav = verificar_taverna_ativa(client)
    if em_tav:
        if seg_tav > 0:
            log.info(f"  Já em taverna ({fmt_t(seg_tav)}) — aguardando conclusão antes de encerrar...")
            time.sleep(seg_tav + 30)
        sair_taverna(client)

    # 2. Compra armadura com todo o gold
    try:
        qtd, preco, nome = comprar_armadura_barata(client)
        if qtd > 0:
            log.info(f"  Gold gasto em armadura: {qtd}x {nome} @ {preco}g")
    except Exception as e:
        log.warning(f"  Compra armadura: erro — {e}")

    # 3. Entra na taverna em loop até voltar ao horário de operação
    while esta_fora_horario():
        horas_restantes = calcular_horas_ate_inicio()
        log.info(f"  ⏳ {horas_restantes:.1f}h até {HORARIO_INICIO} — entrando na taverna...")

        # Escolhe filter com jobs de duração mais próxima do tempo restante
        if horas_restantes >= 10:
            filter_id, horas_max = 4, 12
        elif horas_restantes >= 7:
            filter_id, horas_max = 3, 9
        elif horas_restantes >= 4:
            filter_id, horas_max = 2, 6
        else:
            filter_id, horas_max = 1, 3

        jobs = parsear_taverna(client, horas_max=horas_max, filter_id=filter_id)
        if not jobs:
            # Tenta filter menor como fallback
            for fb_filter, fb_max in [(3, 9), (2, 6), (1, 3)]:
                if fb_filter < filter_id:
                    jobs = parsear_taverna(client, horas_max=fb_max, filter_id=fb_filter)
                    if jobs:
                        break

        if not jobs:
            log.warning(f"  Sem jobs na taverna — dormindo 30min e tentando novamente")
            time.sleep(1800)
            continue

        # Pega o job de maior duração disponível (para dormir o máximo)
        melhor = max(jobs, key=lambda j: j["horas"])
        try:
            client.get_url(melhor["url"])
            log.info(f"  🌙 Taverna noturna: {melhor['horas']}h aceito (+{melhor['gold']}g) — dormindo...")
            time.sleep(melhor["horas"] * 3600)
            # Sai da taverna para coletar gold
            sair_taverna(client)
            gold_pos, _ = parsear_gold_gems(client)
            log.info(f"  Gold após taverna noturna: {gold_pos}g")
        except Exception as e:
            log.warning(f"  Erro na taverna noturna: {e} — tentando novamente em 5min")
            time.sleep(300)

    log.info(f"⏰ Horário de operação retomado ({HORARIO_INICIO}) — voltando ao ciclo normal")


def verificar_treinamento(client):
    """
    Treina atributos em loop: sempre a mais barata primeiro, re-verifica após cada treino.
    - Só executa se TREINAR_ATRIBUTOS=True no config
    - Não executa se personagem estiver em missão na taverna
    - BUILD_1MAO=False (build 2 mãos) → nunca treina Agilidade
    - Retorna lista de atributos treinados
    """
    if not TREINAR_ATRIBUTOS:
        return []
    em_taverna, _ = verificar_taverna_ativa(client)
    if em_taverna:
        log.debug("  Treinamento: pulando — personagem em missão na taverna")
        return []

    # Pausa treinamento se há um alvo de compra e gold insuficiente (prioridade: item > pedra > anel > amuleto)
    estado_t = carregar_estado()
    gold_reservado = 0
    motivo_reserva = None
    if COMPRAR_EQUIPAMENTO:
        gold_t = estado_t.get("gold_atual", 0)
        item_alvo    = estado_t.get("item_alvo")
        pedra_alvo   = estado_t.get("pedra_alvo")
        anel_alvo    = estado_t.get("anel_alvo")
        amuleto_alvo = estado_t.get("amuleto_alvo")
        if item_alvo:
            gb = item_alvo.get("gold_bruto", item_alvo.get("gold_necessario", 0))
            if 0 < gb < 50:
                # Preço inválido (parsing antigo) — descarta e re-escaneia
                log.warning(f"  Treinamento: item_alvo '{item_alvo['nome']}' com preço inválido ({gb}g) — descartando")
                del estado_t["item_alvo"]
                salvar_estado(estado_t)
                try:
                    verificar_alvo_equipamento(client, estado_t)
                    estado_t = carregar_estado()
                    item_alvo = estado_t.get("item_alvo")
                except Exception:
                    item_alvo = None
            if item_alvo:
                gb = item_alvo.get("gold_bruto", item_alvo.get("gold_necessario", 0))
                if gb >= 50:
                    # Reserva gold_necessario (não gold_bruto): o bot vende o atual para compensar.
                    # Se gold_necessario=0 (item de gema chegou ao catálogo), usa gold_bruto como reserva.
                    gn = item_alvo.get("gold_necessario", gb)
                    gold_reservado = gn if gn > 0 else gb
                    motivo_reserva = item_alvo["nome"]
        elif pedra_alvo:
            gold_reservado = pedra_alvo["gold_necessario"]
            motivo_reserva = pedra_alvo["nome"]
        elif anel_alvo:
            gold_reservado = anel_alvo["gold_necessario"]
            motivo_reserva = f"{anel_alvo['quantidade']}x {anel_alvo['nome']}"
        elif amuleto_alvo:
            gold_reservado = amuleto_alvo["gold_necessario"]
            motivo_reserva = amuleto_alvo["nome"]
        # Pausa treino sempre que há um alvo de compra com preço válido.
        # Se gold < reservado: acumulando. Se gold >= reservado: compra deveria ter acontecido
        # mas falhou (url_compra=None, rescan falhou) — não gasta o gold em treino.
        if gold_reservado > 0:
            if gold_t < gold_reservado:
                log.info(f"  Treinamento pausado — guardando gold para {motivo_reserva} "
                         f"({gold_t}g / {gold_reservado}g)")
            else:
                log.info(f"  Treinamento pausado — aguardando compra de {motivo_reserva} "
                         f"({gold_t}g disponível, compra pendente)")
            return []

    nomes = {
        "staerke": "Força", "ausdauer": "Resistência",
        "geschicklichkeit": "Agilidade", "kampfkunst": "Arte de combate",
        "parieren": "Bloqueio",
    }
    treinados = []

    while True:
        try:
            soup = client.get("/train/", fragment=False)
        except Exception as e:
            log.warning(f"Treinamento: erro ao carregar página — {e}")
            break

        # Coleta todos os links disponíveis com seus custos
        candidatos = []
        links = soup.find_all("a",
                              href=lambda h: h and "/train/" in h and h.rstrip("/") != "/train")
        log.debug(f"  Treinamento: {len(links)} link(s) encontrado(s) na página")
        for a in links:
            href = a.get("href", "")
            if not href or href.rstrip("/") in ("/train",):
                continue
            # Pula Agilidade se build 2 mãos
            if "geschicklichkeit" in href and not BUILD_1MAO:
                continue
            texto = a.get_text(separator=" ")
            custo_m = re.search(r"[\d.,]+", texto)
            custo = int(custo_m.group().replace(".", "").replace(",", "")) if custo_m else 0
            segmento = href.strip("/").split("/")[-1]
            candidatos.append({"href": href, "custo": custo, "nome": nomes.get(segmento, segmento)})

        if not candidatos:
            break  # nenhum disponível (sem gold ou tudo no máximo)

        # Treina a mais barata
        candidatos.sort(key=lambda x: x["custo"])
        alvo = candidatos[0]
        log.info(f"  Treinando {alvo['nome']} (custo: {alvo['custo']}g)...")
        try:
            href_rel = alvo["href"]
            if href_rel.startswith("http"):
                from urllib.parse import urlparse
                href_rel = urlparse(href_rel).path
            client.get(href_rel, fragment=False)
            treinados.append(alvo["nome"])
            log.info(f"  ✓ {alvo['nome']} treinado!")
        except Exception as e:
            log.warning(f"  Treinamento {alvo['nome']}: erro — {e}")
            break

    return treinados


def distribuir_pontos_skill(client):
    """
    Distribui pontos de skill disponíveis em /skills/ conforme BUILD_TIPO.
    - 2h: todos os pontos em zweihand
    - 1h: minimiza diferença entre einhand e ruestung (empate → ruestung)
    Não tem CD — pode ser chamado a qualquer momento, inclusive durante taverna.
    """
    if not DISTRIBUIR_SKILLS:
        return []
    try:
        soup = client.get("/skills/", fragment=False)
    except Exception as e:
        log.warning(f"Skills: erro ao carregar página — {e}")
        return []

    txt = soup.get_text()
    m = re.search(r"Available skill points:\s*(\d+)", txt)
    if not m:
        m = re.search(r"Skillpoints disponíveis:\s*(\d+)", txt)          # PT (int4, pt4...)
    if not m:
        m = re.search(r"Pontos de habilidade disponíveis:\s*(\d+)", txt) # PT alternativo
    if not m:
        m = re.search(r"Verfügbare Fertigkeitspunkte:\s*(\d+)", txt)     # DE
    if not m:
        m = re.search(r"Puntos de habilidad disponibles:\s*(\d+)", txt)  # ES
    if not m:
        m = re.search(r"Skillpunten beschikbaar:\s*(\d+)", txt)          # NL
    if not m:
        m = re.search(r"Disponibili punti abilità:\s*(\d+)", txt)        # IT
    if not m:
        # busca genérica: qualquer número após texto com "skill" ou "punkt" ou "point"
        m = re.search(r"(?:Skillpoints?|skill points?|Fertigkeitspunkte|punti abilità)[^\d]{0,30}(\d+)", txt, re.IGNORECASE)
    if not m:
        log.warning(f"Skills: nenhum padrão de pontos encontrado na página /skills/ — verificar texto da página")
        return []
    pontos = int(m.group(1))
    if pontos <= 0:
        return []

    log.info(f"  Skills: {pontos} ponto(s) disponível(eis) — build {BUILD_TIPO}")
    distribuidos = []

    if BUILD_TIPO == "2h":
        for _ in range(pontos):
            try:
                client.get("/skills/zweihand/", fragment=False)
                distribuidos.append("zweihand")
            except Exception as e:
                log.warning(f"  Skills zweihand: erro — {e}")
                break
    else:  # 1h
        # Lê valores atuais
        e_el = soup.find(id="c-einhand")
        r_el = soup.find(id="c-ruestung")
        einhand  = int(e_el.get("data-skill", 0)) if e_el else 0
        ruestung = int(r_el.get("data-skill", 0)) if r_el else 0

        for _ in range(pontos):
            # Dá ponto ao mais baixo; empate → ruestung (cobre armor + shield)
            if einhand < ruestung:
                url, nome = "/skills/einhand/", "einhand"
                einhand += 1
            else:
                url, nome = "/skills/ruestung/", "ruestung"
                ruestung += 1
            try:
                client.get(url, fragment=False)
                distribuidos.append(nome)
            except Exception as e:
                log.warning(f"  Skills {nome}: erro — {e}")
                break

    if distribuidos:
        log.info(f"  ✓ Skills distribuídas: {', '.join(distribuidos)}")
        # Novas skills podem desbloquear itens — re-escaneia lojas imediatamente
        if COMPRAR_EQUIPAMENTO:
            try:
                verificar_alvo_equipamento(client, carregar_estado())
            except Exception as e:
                log.warning(f"  Re-scan lojas pós-skill: erro — {e}")
    return distribuidos


def verificar_raubzug(client):
    soup = client.get("/raubzug/")
    txt = soup.get_text()
    resultado = {"livre": True, "segundos_cd": 0, "missao_concluida": False,
                 "csrf_missao": "", "minutos_usados_hoje": 0, "soup": soup}

    for script in soup.find_all("script"):
        s = script.string or ""
        if "Secondscounter" not in s: continue
        m = re.search(r"var Secondscounter\s*=\s*(-?\d+)", s)
        if not m: continue
        val = int(m.group(1))
        if val > 0:
            resultado["livre"] = False
            resultado["segundos_cd"] = val
            log.info(f"CD ativo: {fmt_t(val)} restantes")
        elif val < 0:
            resultado["missao_concluida"] = True
            log.info("Missão concluída — pronta para nova ação")
        break

    # Tenta detectar "Already used: X of Y minutes" (total diário visível)
    m2 = re.search(r"Already used:\s*(\d+)\s*of\s*(\d+)\s*min", txt, re.IGNORECASE)
    if m2:
        resultado["minutos_usados_hoje"] = int(m2.group(1))
        resultado["minutos_limite_dia"]  = int(m2.group(2))
    else:
        m2b = re.search(r"Already used:\s*(\d+)\s*minutes", txt, re.IGNORECASE)
        if m2b:
            resultado["minutos_usados_hoje"] = int(m2b.group(1))

    # Detecta cota diária esgotada
    frases_cota = [
        "já usou todo o seu tempo de missão",
        "usou todo o seu tempo",
        "Somente amanhã você poderá",
        "you have used all your mission time",
        "no more missions today",
    ]
    if any(f.lower() in txt.lower() for f in frases_cota):
        resultado["cota_diaria"] = True
        resultado["livre"] = False
        log.info("Cota diária de missões esgotada")
        # Tenta extrair tempo até próxima atualização
        m3 = re.search(r"próxima atualização[:\s]+(\d+:\d+:\d+)", txt)
        if m3:
            resultado["tempo_reset"] = m3.group(1)
            log.info(f"  Reset em: {m3.group(1)}")

    # Verifica se o form de missão está disponível e detecta minutos disponíveis
    missao_disponivel = False
    for form in soup.find_all("form"):
        if form.find("input", {"name": "ac", "value": "raubzug"}) and \
           form.find("input", {"name": "sac", "value": "mission"}):
            inp = form.find("input", {"name": "csrftoken"})
            if inp: resultado["csrf_missao"] = inp.get("value", "")
            # Detecta max minutos disponíveis pelo maior option do select jagdzeit
            sel = form.find("select", {"name": "jagdzeit"})
            if sel:
                missao_disponivel = True
                opcoes = [int(o.get("value")) for o in sel.find_all("option")
                          if o.get("value", "").isdigit()]
                if opcoes:
                    resultado["minutos_disponiveis"] = max(opcoes)
            break

    if not missao_disponivel and not resultado.get("cota_diaria") and resultado["livre"]:
        log.debug("Form de missão sem select jagdzeit — cota pode estar esgotada")
        resultado["minutos_disponiveis"] = 0

    return resultado

# ═══════════════════════════════════════════
# MISSÕES
# ═══════════════════════════════════════════
def gerenciar_missao(client, dry_run=False):
    estado = carregar_estado()

    rv = verificar_raubzug(client)

    if rv.get("cota_diaria"):
        reset = rv.get("tempo_reset", "amanhã")
        log.info(f"Cota diária de missões esgotada — reset: {reset}")
        return {"status": "cota_diaria", "reset": reset}

    if not rv["livre"]:
        fim = agora() + timedelta(seconds=rv["segundos_cd"])
        log.info(f"Em CD — livre às {fim:%H:%M:%S}")
        return {"status": "em_cd", "termina_em": fim.isoformat(), "segundos": rv["segundos_cd"]}

    # Detecta limite diário da página (sem IS_PREMIUM hardcoded)
    minutos_usados    = rv["minutos_usados_hoje"] or estado.get("minutos_missao_hoje", 0)
    minutos_disponiveis = rv.get("minutos_disponiveis")  # max option do select jagdzeit

    if minutos_disponiveis is not None:
        # Página mostra o que resta → limit = usados + disponíveis
        limite_min = minutos_usados + minutos_disponiveis
        minutos_rest = minutos_disponiveis
    elif rv.get("minutos_limite_dia"):
        # Página mostra "X of Y minutes"
        limite_min = rv["minutos_limite_dia"]
        minutos_rest = limite_min - minutos_usados
    else:
        # Fallback: se form não tem select, cota esgotada
        log.info("Cota diária atingida (form sem jagdzeit disponível)")
        return {"status": "cota_diaria", "minutos_usados": minutos_usados}

    if minutos_rest <= 0:
        log.info(f"Cota diária atingida ({minutos_usados}/{limite_min}min)")
        return {"status": "cota_diaria", "minutos_usados": minutos_usados}

    jagdzeit = 10
    alin = MISSAO_ALINHAMENTO
    if alin == "bem":
        gesinnung = "1"
    elif alin == "mal":
        gesinnung = "2"
    else:  # "alternado"
        gesinnung = "1" if estado.get("missoes_hoje", 0) % 2 == 0 else "2"
    label_alin = {"1": "bem ✓", "2": "mal ✗"}.get(gesinnung, "?")
    log.info(f"Missão: {jagdzeit}min | {label_alin} | usados={minutos_usados}/{limite_min}min disponíveis={minutos_disponiveis}")

    if dry_run:
        return {"status": "dry_run", "jagdzeit": jagdzeit, "minutos_rest": minutos_rest}

    csrf = rv["csrf_missao"]
    data = {"csrftoken": csrf, "ac": "raubzug", "sac": "mission",
            "gesinnung": gesinnung, "jagdzeit": str(jagdzeit)}
    r = client.session.post(BASE_URL + "/raubzug/", data=data, timeout=15)
    if r.status_code == 403:
        log.warning("403 na missão — verificando se cota esgotada...")
        rv2 = verificar_raubzug(client)
        # Se page não tem mais form disponível, cota esgotada
        if rv2.get("minutos_disponiveis") == 0 or not rv2.get("csrf_missao"):
            min_usados2 = rv2.get("minutos_usados_hoje", 0) or minutos_usados
            log.info(f"Cota diária confirmada pelo servidor ({min_usados2}/{limite_min}min)")
            return {"status": "cota_diaria", "minutos_usados": min_usados2}
        # Tenta uma vez mais com CSRF novo
        data["csrftoken"] = rv2["csrf_missao"]
        r = client.session.post(BASE_URL + "/raubzug/", data=data, timeout=15)
        if r.status_code == 403:
            log.warning("403 persistente — assumindo cota esgotada")
            return {"status": "cota_diaria", "minutos_usados": minutos_usados}
    r.raise_for_status()

    estado["minutos_missao_hoje"] = minutos_usados + jagdzeit
    estado["missoes_hoje"] = estado.get("missoes_hoje", 0) + 1
    salvar_estado(estado)

    res = {"status": "iniciada", "jagdzeit": jagdzeit, "gesinnung": gesinnung,
           "minutos_hoje": estado["minutos_missao_hoje"], "limite": limite_min}
    atualizar_ciclo_file("missao", res)
    log.info(f"Missão iniciada! Hoje: {estado['minutos_missao_hoje']}/{limite_min}min")
    return res

# ═══════════════════════════════════════════
# STATUS DO PERSONAGEM
# ═══════════════════════════════════════════
def parsear_inventario(soup):
    """
    Extrai itens do inventário da página /status/.
    Localiza a seção 'Inventory' (box-top) → box-bg seguinte → <tr class="mobile-cols-2">.
    Nome em <strong>, quantidade em "N item(s) in your inventory." no texto do td.

    O jogo mostra "X items in your inventory" contando TODOS (equipados + bolsa).
    Itens equipados têm <span class="fontsmallred"> ou texto "equipped" no TR.
    Para itens equipados: qtd_bolsa = qtd_total - qtd_equipada (armadura/arma/amuleto=1, aneis=2 max).
    Retorna dict {nome: qtd_na_bolsa}. Compatível com 'if nome in inventario'.
    """
    _SLOTS_EQUIPADOS = {
        # aneis: máximo 2 equipados simultaneamente
        "anel": 2, "ring": 2, "ringe": 2, "anillo": 2,
    }
    result = {}
    for boxtop in soup.find_all("div", class_="box-top"):
        txt = boxtop.get_text().strip().lower()
        if "inventory" in txt or "inventar" in txt:
            boxbg = boxtop.find_next_sibling("div", class_="box-bg")
            if not boxbg:
                break
            for tr in boxbg.find_all("tr", class_="mobile-cols-2"):
                strong = tr.find("strong")
                if not strong:
                    continue
                nome = strong.get_text(strip=True)
                if not nome:
                    continue
                tr_txt = tr.get_text(separator=" ", strip=True)
                # Extrai quantidade total ("N item(s) in your inventory.")
                qtd_total = 1
                td = strong.find_parent("td")
                if td:
                    m = re.search(r"(\d+)\s+item", td.get_text(), re.IGNORECASE)
                    if m:
                        qtd_total = int(m.group(1))
                # Verifica se está equipado — subtrai slot equipado do total
                equipado = bool(re.search(
                    r"equipped|equipado|ausger[üu]stet",
                    tr_txt, re.IGNORECASE
                ))
                if equipado:
                    # Determina quantos slots esse tipo ocupa
                    nome_lower = nome.lower()
                    slots = 1  # default: arma, armadura, amuleto = 1 slot
                    for chave, n in _SLOTS_EQUIPADOS.items():
                        if chave in nome_lower:
                            slots = n
                            break
                    qtd_bolsa = max(0, qtd_total - slots)
                else:
                    qtd_bolsa = qtd_total
                if qtd_bolsa > 0:
                    result[nome] = result.get(nome, 0) + qtd_bolsa
            break
    return result


def parsear_gold_gems(client):
    """
    Gold e pedras ficam no HTML completo (sem ?fragment=1).
    IDs exatos no HTML: 
      <span id="gold-header">3.228</span>
      <span id="edelsteine-header">12</span>
    """
    try:
        r = client.session.get(BASE_URL + "/status/", timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        gold_el = soup.find(id="gold-header")
        gems_el = soup.find(id="edelsteine-header")

        gold = parse_num(gold_el.get_text()) if gold_el else 0
        gems = int(gems_el.get_text().strip()) if gems_el else 0

        log.info(f"Gold na conta: {gold}g | Pedras: {gems}")
        return gold, gems
    except Exception as e:
        log.warning(f"Erro ao buscar gold/gems: {e}")
        return 0, 0


def get_my_clan_id(client):
    """Lê meu clan_id da página de clan."""
    try:
        soup = client.get("/clan/")
        for tag in soup.find_all("a", href=True):
            m = re.search(r"/clan/(\d+)/", tag["href"])
            if m:
                cid = int(m.group(1))
                if cid > 0:
                    return cid
    except Exception:
        pass
    return None


def parsear_status(soup):
    """
    Extrai tudo do personagem: level, XP, HP, gold, combates,
    atributos (força, AC, bloqueio...) e moral.
    Atualiza MY_STATS globalmente para manter level_min_xp() correto.
    """
    txt = soup.get_text()

    def ex(label):
        m = re.search(rf"{re.escape(label)}\s*([\d.]+)", txt)
        return parse_num(m.group(1)) if m else 0

    def extrair_attr(nomes):
        """
        Tooltips do status próprio: "Strength: 52 + 2" ou "Dexterity: 5 - 11" ou "Parry: 71"
        Calcula base +/- modificador para obter o valor final com itens.
        Tooltips do perfil alheio: "Arte de combate: (172)" — só tem o valor.
        """
        for tag in soup.find_all(attrs={"data-tooltip": True}):
            tip = tag["data-tooltip"]
            for n in nomes:
                if n.lower() in tip.lower():
                    # Tenta padrão "Label: BASE + MOD" ou "Label: BASE - MOD"
                    m = re.search(r"[:( ]\s*(\d+)\s*([+-])\s*(\d+)", tip)
                    if m:
                        base = int(m.group(1))
                        sinal = 1 if m.group(2) == "+" else -1
                        mod  = int(m.group(3))
                        return base + sinal * mod
                    # Fallback: só o número base (sem modificador)
                    m = re.search(r"[:( ]\s*(\d+)", tip)
                    if m: return int(m.group(1))
        return 0

    xp_atual = xp_total = hp_atual = hp_total = 0
    for tag in soup.find_all(attrs={"data-tooltip": True}):
        tip = tag["data-tooltip"]
        if "Experience:" in tip:
            m = re.search(r"Experience:\s*([\d,]+)\s*of\s*([\d,]+)", tip)
            if m:
                xp_atual = parse_num(m.group(1))
                xp_total = parse_num(m.group(2))
        if "Health points:" in tip:
            m = re.search(r"Health points:\s*([\d,. ]+)\s*of\s*([\d.]+)", tip)
            if m:
                hp_atual = int(float(m.group(1).replace(".", "").replace(",", ".").strip()))
                hp_total = parse_num(m.group(2))

    level = 0
    for tag in soup.find_all(attrs={"data-tooltip": True}):
        if "Level:" in tag.get("data-tooltip", ""):
            m = re.search(r"Level:\s*(\d+)", tag["data-tooltip"])
            if m: level = int(m.group(1))

    # Extrai moral
    moral = ""
    for tag in soup.find_all(attrs={"data-tooltip": True}):
        tip = tag.get("data-tooltip", "")
        if "Moral:" in tip:
            m = re.search(r"Moral:\s*(.+?)\s*\(", tip)
            if m: moral = m.group(1).strip()

    # Extrai gold atual da página (valor da mercadoria ou similar)
    # O gold atual fica na seção de estatísticas
    gold_atual = 0
    m_gold = re.search(r"Total das preciosidades:[^\d]*(\d[\d.]*)", txt)
    # Gold na conta não está diretamente no status — usar estado salvo

    forca       = extrair_attr(["Strength:", "Força:"])
    resistencia = extrair_attr(["Stamina:", "Resistência:"])
    agilidade   = extrair_attr(["Dexterity:", "Agilidade:"])
    arte_comb   = extrair_attr(["Fighting ability:", "Arte de combate:"])
    bloqueio    = extrair_attr(["Parry:", "Bloqueio:"])
    sk_armadura = extrair_attr(["Armour skill:"])
    sk_1mao     = extrair_attr(["One-handed attack:"])
    sk_2maos    = extrair_attr(["Two-handed attack:"])

    # Detecta imunidade ativa no status
    imunidade_seg_restante = 0
    for script in soup.find_all("script"):
        st = script.string or ""
        # KF usa Secondscounter para imunidade também
        m_imun = re.search(r"imunit[^=]*=\s*(\d+)", st, re.IGNORECASE)
        if m_imun:
            imunidade_seg_restante = int(m_imun.group(1))
            break
    # Fallback: texto "imunizado por X minutos"
    if not imunidade_seg_restante:
        m_txt = re.search(r"imunizado[^\d]*(\d+)\s*minutos?", txt, re.IGNORECASE)
        if m_txt:
            imunidade_seg_restante = int(m_txt.group(1)) * 60
        else:
            m_txt2 = re.search(r"immunized[^\d]*(\d+)\s*minute", txt, re.IGNORECASE)
            if m_txt2:
                imunidade_seg_restante = int(m_txt2.group(1)) * 60

    s = {
        "timestamp": agora().isoformat(),
        "level": level,
        "xp_atual": xp_atual, "xp_total": xp_total,
        "hp_atual": hp_atual, "hp_total": hp_total,
        "combates":      ex("Combates:"),
        "vitorias":      ex("Vencidos:"),
        "derrotas":      ex("Derrotas:"),
        "gold_ganho":    ex("Ouro ganho:"),
        "gold_perdido":  ex("Ouro perdido:"),
        "preciosidades": ex("Total das preciosidades:"),
        "moral": moral,
        "forca": forca, "resistencia": resistencia,
        "agilidade": agilidade, "arte_combate": arte_comb, "bloqueio": bloqueio,
        "sk_armadura": sk_armadura, "sk_1mao": sk_1mao, "sk_2maos": sk_2maos,
        "imunidade_seg": imunidade_seg_restante,
    }

    # Atualiza MY_STATS globalmente — garante level_min_xp() correto após upagem
    if level > 0:
        global MY_STATS
        MY_STATS["level"] = level
        if forca > 0:
            MY_STATS["forca"]        = forca
            MY_STATS["resistencia"]  = resistencia
            MY_STATS["agilidade"]    = agilidade
            MY_STATS["arte_combate"] = arte_comb
            MY_STATS["bloqueio"]     = bloqueio
            MY_STATS["sk_armadura"]  = sk_armadura
            MY_STATS["sk_1mao"]      = sk_1mao
            MY_STATS["sk_2maos"]     = sk_2maos
        log.info(f"MY_STATS atualizado: Lv{level} | AC={arte_comb} Blq={bloqueio} | min_xp_lv={level_min_xp()}")

    return s

# ═══════════════════════════════════════════
# LOOPS
# ═══════════════════════════════════════════

def recalcular_scores_cache():
    """
    Recalcula o score de todos os perfis no cache usando os stats atuais
    do personagem + modelo aprendido.
    Chamado automaticamente quando MY_STATS muda (level up, atributo investido).
    Loga quem mudou de categoria (EVITAR↔ATACAR).
    """
    cache = carregar_perfis_cache()
    perfis = cache.get("perfis", {})
    if not perfis:
        return 0

    modelo = carregar_modelo()
    combates = carregar_combates_srv()

    # Peso do aprendizado aumenta com mais combates
    n_combates = len(combates)
    if n_combates >= 500:
        peso_modelo = 0.60
    elif n_combates >= 200:
        peso_modelo = 0.45
    elif n_combates >= 50:
        peso_modelo = 0.30
    else:
        peso_modelo = 0.0  # sem dados suficientes, só fórmula

    recalculados = 0
    mudancas_atacar = []   # EVITAR → ATACAR
    mudancas_evitar = []   # ATACAR → EVITAR

    for uid, perfil in perfis.items():
        score_antigo = perfil.get("_score", None)
        rec_antiga   = perfil.get("_rec", None)

        # 100% simulador — igual ao avaliar_alvo
        av = avaliar_alvo(perfil)
        score_novo = av["score"]
        rec_nova   = av["recomendacao"]

        # Atualiza no cache
        perfil["_score"] = score_novo
        perfil["_rec"]   = rec_nova
        recalculados += 1

        # Detecta mudança de categoria
        if score_antigo is not None and rec_antiga is not None:
            foi_evitar  = rec_antiga == "EVITAR"
            foi_atacar  = rec_antiga == "ATACAR"
            agora_atacar = rec_nova == "ATACAR"
            agora_evitar = rec_nova == "EVITAR"

            if foi_evitar and agora_atacar:
                mudancas_atacar.append(
                    f"{perfil.get('nome','?')} Lv{perfil.get('level','?')} "
                    f"({score_antigo}→{score_novo})"
                )
            elif foi_atacar and agora_evitar:
                mudancas_evitar.append(
                    f"{perfil.get('nome','?')} Lv{perfil.get('level','?')} "
                    f"({score_antigo}→{score_novo})"
                )

    cache["perfis"] = perfis
    cache["score_recalculado_em"] = agora().isoformat()
    cache["peso_modelo_usado"] = peso_modelo
    salvar_perfis_cache(cache)

    log.info(f"Scores recalculados: {recalculados} perfis | "
             f"Modelo: {peso_modelo*100:.0f}% | Combates: {n_combates}")

    if mudancas_atacar:
        log.info(f"  ✅ Passaram para ATACAR ({len(mudancas_atacar)}): "
                 + " | ".join(mudancas_atacar[:10]))
    if mudancas_evitar:
        log.info(f"  ⛔ Passaram para EVITAR ({len(mudancas_evitar)}): "
                 + " | ".join(mudancas_evitar[:10]))

    return recalculados

def recarregar_config():
    """Relê config.json e atualiza globals dinâmicos sem reiniciar."""
    if not os.path.exists("config.json"):
        return
    try:
        with open("config.json", encoding="utf-8") as f:
            cfg = json.load(f)
        changed = []
        for field, key, cast in [
            ("gold_min_pig",    "GOLD_MIN_PIG",    int),
            ("perda_xp_max",    "PERDA_XP_MAX",    int),
            ("gold_ignorar_xp", "GOLD_IGNORAR_XP", int),
        ]:
            if field in cfg:
                novo = cast(cfg[field])
                if globals().get(key) != novo:
                    changed.append(f"{key}: {globals().get(key)} -> {novo}")
                    globals()[key] = novo
        if changed:
            log.info(f"Config recarregada: {', '.join(changed)}")
    except Exception as e:
        log.warning(f"Erro ao recarregar config: {e}")

def loop_lento(client):
    """A cada 1h: status do personagem + atributos + recalculo de scores + skills."""
    while True:
        log.info("\n[LENTO] Iniciando ciclo horário...")
        try:
            estado = carregar_estado()
            # Recarrega config.json para pegar mudanças feitas pelo launcher
            recarregar_config()

            # Status do personagem → dashboard
            try:
                status = parsear_status(client.get("/status/"))
                atualizar_ciclo_file("status", status)
                log.info(f"Status: Lv{status['level']} | {status['vitorias']}V/{status['derrotas']}D | {status['preciosidades']} prec")

                # Atualiza clan_id periodicamente
                clan_id_atual = get_my_clan_id(client)
                if clan_id_atual != estado.get("meu_clan_id"):
                    log.info(f"Guild atualizada: {estado.get('meu_clan_id')} → {clan_id_atual}")
                    estado["meu_clan_id"] = clan_id_atual
                    salvar_estado(estado)

                # ── Detecta mudança de atributos e recalcula scores ──────────
                stats_chave = ("level", "arte_combate", "bloqueio", "forca", "resistencia")
                stats_antes = {k: estado.get(f"_last_{k}", 0) for k in stats_chave}
                stats_agora = {k: status.get(k, 0) for k in stats_chave}
                mudou = any(stats_agora[k] != stats_antes[k] for k in stats_chave)

                if mudou:
                    mudancas = [f"{k}: {stats_antes[k]}→{stats_agora[k]}"
                                for k in stats_chave if stats_agora[k] != stats_antes[k]]
                    log.info(f"⬆ Atributos mudaram: {', '.join(mudancas)} — recalculando scores...")
                    recalcular_scores_cache()
                    # Salva stats atuais para próxima comparação
                    for k in stats_chave:
                        estado[f"_last_{k}"] = stats_agora[k]
                    salvar_estado(estado)
                    # Subiu de level → distribui pontos de skill imediatamente
                    if stats_agora["level"] != stats_antes["level"]:
                        try:
                            distribuir_pontos_skill(client)
                        except Exception as e:
                            log.warning(f"Skills pós-level: erro — {e}")
                        try:
                            verificar_alvo_equipamento(client, carregar_estado())
                        except Exception as e:
                            log.warning(f"Alvo equipamento pós-level: erro — {e}")
                        try:
                            verificar_alvo_pedra(client, carregar_estado())
                        except Exception as e:
                            log.warning(f"Alvo pedra pós-level: erro — {e}")
                        try:
                            verificar_alvo_anel(client, carregar_estado())
                        except Exception as e:
                            log.warning(f"Alvo anel pós-level: erro — {e}")
                        try:
                            verificar_alvo_amuleto(client, carregar_estado())
                        except Exception as e:
                            log.warning(f"Alvo amuleto pós-level: erro — {e}")
                else:
                    # Recalcula scores periodicamente mesmo sem mudança de atributo
                    # (modelo pode ter melhorado com novos combates)
                    ultima_rec = estado.get("_score_recalc_em")
                    horas_desde = seg_desde(ultima_rec) / 3600 if ultima_rec else 999
                    if horas_desde >= 6:
                        log.info("Recalculando scores (atualização periódica do modelo)...")
                        recalcular_scores_cache()
                        estado["_score_recalc_em"] = agora().isoformat()
                        salvar_estado(estado)

            except Exception as e:
                log.error(f"Erro status: {e}")

            # Distribui pontos de skill pendentes (fallback horário)
            try:
                distribuir_pontos_skill(client)
            except Exception as e:
                log.warning(f"Skills loop lento: erro — {e}")

        except Exception as e:
            log.error(f"Erro loop lento: {e}", exc_info=True)

        time.sleep(INTERVALO_LENTO_SEG)


def loop_ranking(client):
    """
    Loop independente (1h): scrape ranking + pig list + cache de perfis.
    Nunca bloqueia por taverna ou missão — só faz leitura de dados públicos.
    """
    while True:
        try:
            log.info("\n[RANKING] Coletando ranking e atualizando pig list...")
            jogadores = scrape_ranking(client)
            if jogadores:
                salvar_snapshot(jogadores)
                snaps = carregar_snapshots()
                if len(snaps) >= 2:
                    pig_list = carregar_pig_list()
                    pig_list = atualizar_pig_list(
                        pig_list, snaps[-2]["jogadores"], snaps[-1]["jogadores"], carregar_estado()
                    )
                    salvar_pig_list(pig_list)
                    atualizar_ciclo_file("pig_list", pig_list)
                    log.info(f"[RANKING] Pig list atualizada: {len(pig_list)} candidatos")
                else:
                    log.info("[RANKING] Aguardando 2º snapshot para comparar (próxima hora)")

            # Cache de perfis às 3h (ou fallback se +25h sem atualizar)
            hora_atual = agora().hour
            cache_velho = cache_precisa_atualizar()
            janela_3h = abs(hora_atual - HORA_CACHE_PERFIS) <= 1
            if cache_velho and janela_3h:
                log.info(f"[RANKING] Atualizando cache de perfis (hora={hora_atual}h)...")
                coletar_perfis_cache(client)
            elif cache_velho and seg_desde(carregar_perfis_cache().get("atualizado_em", "")) / 3600 >= 25:
                log.warning("[RANKING] Cache +25h sem atualizar — forçando varredura...")
                coletar_perfis_cache(client)

        except Exception as e:
            log.error(f"Erro loop ranking: {e}", exc_info=True)

        time.sleep(INTERVALO_LENTO_SEG)


def parsear_taverna(client, horas_max=1, filter_id=1):
    """
    Lê os jobs disponíveis na taverna usando filtro por duração.
    filter_id=1 → 1-3h | filter_id=2 → 4-6h | filter_id=3 → 7-9h | filter_id=4 → 10-12h
    Retorna lista de {horas, gold, url}
    """
    import re as _re
    try:
        url_taverna = f"/job/?filter={filter_id}"
        soup = client.get(url_taverna, fragment=False)  # fragment=False para ter o HTML completo com a tabela de jobs
        jobs = []
        for row in soup.find_all("tr"):
            link = row.find("a", href=lambda h: h and "/job/startjob/" in h)
            if not link:
                continue
            url = link["href"]

            # Horas: busca <strong> com número simples dentro da linha
            # O HTML tem: <strong>1</strong> para horas
            horas = 0
            for strong in row.find_all("strong"):
                txt = strong.get_text(strip=True)
                if txt.isdigit() and 1 <= int(txt) <= 12:
                    horas = int(txt)
                    break

            # Gold: primeiro número > 12 (horas vão de 1-12, gold mínimo é 70g)
            # O HTML tem: <strong>70</strong>, <strong>140</strong>, <strong>210</strong> etc.
            gold = 0
            for strong in row.find_all("strong"):
                txt = strong.get_text(strip=True).replace(".", "").replace(",", "")
                if txt.isdigit():
                    v = int(txt)
                    if v > 12:
                        gold = v
                        break

            if horas and gold and horas <= horas_max:
                # Deduplica por URL (linha tem link mobile e desktop)
                if not any(j["url"] == url for j in jobs):
                    jobs.append({"horas": horas, "gold": gold, "url": url})
                    log.debug(f"  Taverna job: {horas}h +{gold}g")

        log.info(f"Taverna ({url_taverna}): {len(jobs)} jobs de até {horas_max}h")
        return jobs
    except Exception as e:
        log.warning(f"Erro parsear_taverna: {e}")
        return []


def aceitar_job_taverna(client, horas_max=1):
    """
    Aceita o job de menor duração DENTRO do limite horas_max.
    Se não houver job dentro do limite, retorna falha — nunca ultrapassa.
    Retorna (ok, horas, gold, msg)
    """
    jobs = parsear_taverna(client, horas_max=horas_max)
    if not jobs:
        return False, 0, 0, "sem jobs disponíveis"

    # Filtra jobs dentro do limite — sem fallback para não ultrapassar
    candidatos = [j for j in jobs if j["horas"] <= horas_max]
    if not candidatos:
        disponiveis = ", ".join(f"{j['horas']}h" for j in jobs)
        return False, 0, 0, f"sem job de {horas_max}h (disponíveis: {disponiveis})"

    # Escolhe o melhor gold/hora dentro do limite
    melhor = max(candidatos, key=lambda j: j["gold"] / j["horas"])

    try:
        client.get_url(melhor["url"])
        log.info(f"🍺 Taverna: job de {melhor['horas']}h aceito (+{melhor['gold']}g)")
        return True, melhor["horas"], melhor["gold"], "ok"
    except Exception as e:
        log.warning(f"Erro aceitar job: {e}")
        return False, 0, 0, str(e)


def verificar_taverna_ativa(client):
    """
    Verifica se o personagem já está em missão na taverna.
    Retorna (em_taverna, segundos_restantes)
    Detecta pelo HTML: Secondscounter = XXXX ou texto 'out on an assignment'
    """
    try:
        soup = client.get("/job/", fragment=False)  # precisa do HTML completo para detectar Secondscounter
        html = str(soup)
        # Detecta contador JS: var Secondscounter = 2927;
        import re as _re
        m = _re.search(r"var Secondscounter\s*=\s*(\d+)", html)
        if m:
            seg = int(m.group(1))
            if seg > 0:
                return True, seg
        # Fallback: texto de missão em andamento
        txt = soup.get_text(" ", strip=True).lower()
        if "out on an assignment" in txt or "ainda em serviço" in txt or "canceljob" in txt:
            return True, 0
        return False, 0
    except Exception as e:
        log.warning(f"Erro verificar_taverna_ativa: {e}")
        return False, 0


def sair_taverna(client):
    """
    Visita a página da taverna para concluir a missão e receber o gold.
    O jogo só credita o gold quando você acessa /job/ após o timer.
    """
    try:
        soup = client.get("/job/", fragment=False)
        txt = soup.get_text(" ", strip=True)
        # Verifica se recebeu gold (página volta à lista normal de jobs)
        if "/job/startjob/" in str(soup):
            log.info("  ✓ Saiu da taverna — gold creditado")
            return True
        # Se ainda mostra contador, ainda não terminou
        if "canceljob" in str(soup) or "Secondscounter" in str(soup):
            log.warning("  ⚠ Taverna ainda ativa ao tentar sair")
            return False
        log.info("  ✓ Saiu da taverna")
        return True
    except Exception as e:
        log.warning(f"  Erro ao sair da taverna: {e}")
        return False


def imunizar_agora(client, estado=None):
    """
    Tenta imunizar com o melhor alvo disponível no cache.
    Esgota TODOS os candidatos disponíveis sem delay entre tentativas.
    Ordem: score >= 80 → 70 → 50. Retorna True se conseguiu.
    """
    if estado is None:
        estado = carregar_estado()
    cache_ok = bool(carregar_perfis_cache().get("perfis"))
    if not cache_ok:
        log.warning("  Cache vazio — não é possível imunizar agora")
        return False
    meu_clan = estado.get("meu_clan_id")
    alvos_tentados = set()  # acumulado entre todos os níveis de score

    for score_min in [SCORE_MIN_IMUNIZACAO, 70, 50]:
        while True:
            alvo = buscar_alvo_imunizacao(client, carregar_estado(), score_min,
                                          excluir=alvos_tentados)
            if not alvo:
                break  # sem mais candidatos nesse nível → tenta nível inferior
            alvos_tentados.add(alvo["user_id"])

            # Verifica disponibilidade real (sem delay)
            ok_i, _, motivo_i = verificar_alvo_antes_de_atacar(
                client, alvo["user_id"], 50, meu_clan)
            if not ok_i:
                log.debug(f"  Alvo {alvo['nome']} indisponível ({motivo_i}) — próximo")
                continue

            res_i = executar_ataque(client, alvo["user_id"])
            if res_i.get("status") == "executado":
                log.info(f"  ✓ Imunizado com {alvo['nome']} Lv{alvo['level']} (score_min={score_min})")
                return True
            else:
                log.warning(f"  Ataque em {alvo['nome']} falhou ({res_i.get('status')}) — próximo")

    log.warning(f"  Não foi possível imunizar — {len(alvos_tentados)} alvos tentados, todos indisponíveis")
    return False


def _taverna_1h(client):
    """
    Sem pig e sem missão disponível:
    1. Imuniza (se imunidade < 5min)
    2. Verifica se já está em missão — se sim, aguarda terminar
    3. Senão, aceita job de 1h; se não tem, fica tentando a cada 1s
    4. Imuniza ao sair
    """
    log.info("⏳ Sem pig e sem missão — ciclo taverna 1h")

    # Passo 1: SEMPRE tenta imunizar antes de entrar na taverna
    # O objetivo é entrar com imunidade máxima (1h) para não ficar descoberto durante a taverna
    # Só pula se imunidade já for >= duração da taverna (1h = 3600s)
    estado = carregar_estado()
    imun = imunidade_restante(estado)
    DURACAO_TAVERNA = 3600  # 1h em segundos
    if imun >= DURACAO_TAVERNA:
        log.info(f"  Imunidade suficiente ({fmt_t(imun)}) — não precisa renovar antes da taverna")
    else:
        log.info(f"  Imunidade insuficiente ({fmt_t(imun)}) — tentando imunizar antes de entrar na taverna...")
        ok_imun = imunizar_agora(client, estado)
        if ok_imun:
            log.info("  ✓ Imunizado — entrando na taverna com proteção máxima")
        else:
            log.warning("  ✗ Sem alvo disponível para imunizar — entrando na taverna mesmo assim")

    # Passo 2: gasta gold em treinamento antes de entrar na taverna
    try:
        treinados = verificar_treinamento(client)
        if treinados:
            log.info(f"  Treinamento pré-taverna: {', '.join(treinados)}")
    except Exception as e:
        log.warning(f"  Treinamento pré-taverna: erro — {e}")

    # Passo 3: verifica se já está em missão ativa
    em_missao, seg_missao = verificar_taverna_ativa(client)
    if em_missao and seg_missao > 0:
        log.info(f"  🍺 Já em missão! Restam {fmt_t(seg_missao)} — aguardando...")
        fim_iso = (agora() + timedelta(seconds=seg_missao)).isoformat()
        atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "taverna",
            "taverna_fim": fim_iso, "taverna_horas": round(seg_missao/3600, 1), "taverna_gold": 0})
        time.sleep(seg_missao + 10)
        atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "ok", "taverna_fim": None})
        log.info("  ✓ Missão concluída — retomando")
    else:
        # Passo 4: aceitar job de 1h (ou esperar aparecer um)
        ok_tab, horas_tab, gold_tab, msg_tab = aceitar_job_taverna(client, horas_max=1)
        if ok_tab:
            log.info(f"  🍺 Taverna: job {horas_tab}h aceito (+{gold_tab}g) — dormindo {horas_tab}h")
            fim_iso = (agora() + timedelta(hours=horas_tab)).isoformat()
            atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "taverna",
                "taverna_fim": fim_iso, "taverna_horas": horas_tab, "taverna_gold": gold_tab})
            time.sleep(horas_tab * 3600)
            sair_taverna(client)
            atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "ok", "taverna_fim": None})
            log.info("  ✓ Job da taverna concluído — retomando")
            if COMPRAR_EQUIPAMENTO:
                try:
                    verificar_alvo_equipamento(client, carregar_estado())
                except Exception as e:
                    log.warning(f"  Re-scan loja pós-taverna: erro — {e}")
            try:
                treinados = verificar_treinamento(client)
                if treinados:
                    log.info(f"  Treinamento pós-taverna: {', '.join(treinados)}")
            except Exception as e:
                log.warning(f"  Treinamento pós-taverna: erro — {e}")
        else:
            # Sem job de 1h — fica tentando a cada 1s
            log.info(f"  Sem job de 1h ({msg_tab}) — aguardando aparecer...")
            for _tent in range(7200):
                time.sleep(1)
                # A cada 30s verifica se já está em missão
                if _tent % 30 == 0:
                    em_m, seg_m = verificar_taverna_ativa(client)
                    if em_m and seg_m > 0:
                        log.info(f"  🍺 Missão detectada! Restam {fmt_t(seg_m)}")
                        fim_m = (agora() + timedelta(seconds=seg_m)).isoformat()
                        atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "taverna",
                            "taverna_fim": fim_m, "taverna_horas": round(seg_m/3600,1), "taverna_gold": 0})
                        time.sleep(seg_m + 10)
                        sair_taverna(client)
                        atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "ok", "taverna_fim": None})
                        log.info("  ✓ Missão concluída — retomando")
                        if COMPRAR_EQUIPAMENTO:
                            try:
                                verificar_alvo_equipamento(client, carregar_estado())
                            except Exception as e:
                                log.warning(f"  Re-scan loja pós-taverna: erro — {e}")
                        try:
                            treinados = verificar_treinamento(client)
                            if treinados:
                                log.info(f"  Treinamento pós-taverna: {', '.join(treinados)}")
                        except Exception as e:
                            log.warning(f"  Treinamento pós-taverna: erro — {e}")
                        break
                # Tenta aceitar job a cada 30s (não a cada 1s para não spammar o servidor)
                if _tent % 30 == 0:
                    ok_r, h_r, g_r, msg_r = aceitar_job_taverna(client, horas_max=1)
                    if ok_r:
                        log.info(f"  🍺 Taverna: job {h_r}h aceito (+{g_r}g) — dormindo {h_r}h")
                        fim_r = (agora() + timedelta(hours=h_r)).isoformat()
                        atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "taverna",
                            "taverna_fim": fim_r, "taverna_horas": h_r, "taverna_gold": g_r})
                        time.sleep(h_r * 3600)
                        sair_taverna(client)
                        atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "ok", "taverna_fim": None})
                        log.info("  ✓ Job da taverna concluído — retomando")
                        if COMPRAR_EQUIPAMENTO:
                            try:
                                verificar_alvo_equipamento(client, carregar_estado())
                            except Exception as e:
                                log.warning(f"  Re-scan loja pós-taverna: erro — {e}")
                        try:
                            treinados = verificar_treinamento(client)
                            if treinados:
                                log.info(f"  Treinamento pós-taverna: {', '.join(treinados)}")
                        except Exception as e:
                            log.warning(f"  Treinamento pós-taverna: erro — {e}")
                        break
                    if _tent % 60 == 0:
                        log.info(f"  Aguardando job 1h... t={_tent+1}")
            else:
                log.warning("  Sem job de 1h após 2h — retomando bot")

    # Passo 4: imunizar ao sair (sempre tenta, independente do timer)
    log.info("  Imunizando ao sair da taverna...")
    imunizar_agora(client)


def loop_acoes(client):
    """
    Loop de ações: dorme enquanto em taverna ou missão/CD, acorda quando libre.
    Ao acordar: varre lojas → compra → HP → treino → ataca pig / imuniza / missão.
    """
    while True:
        try:
            # ── Controle de horário de operação ──────────────────────────────
            if esta_fora_horario():
                log.info(f"⏰ Fora do horário de operação ({HORARIO_INICIO}–{HORARIO_PARADA}) — encerrando")
                rotina_encerramento_noturno(client)
                time.sleep(INTERVALO_RAPIDO_SEG)
                continue

            # ── Taverna: dorme até a missão terminar ─────────────────────────
            em_tav, seg_tav = verificar_taverna_ativa(client)
            if em_tav:
                if seg_tav > 60:
                    log.info(f"  Taverna ativa: {fmt_t(seg_tav)} restantes — dormindo até acabar")
                    time.sleep(seg_tav + 30)
                try:
                    sair_taverna(client)
                except Exception:
                    pass
                continue

            # ── CD de missão/ataque: dorme até liberar ───────────────────────
            rv = verificar_raubzug(client)
            estado = carregar_estado()
            if rv["segundos_cd"] > 0:
                estado["cooldown_seg"] = rv["segundos_cd"]
                salvar_estado(estado)
            atualizar_ciclo_file("estado", estado)

            if not rv["livre"] and rv["segundos_cd"] > 0:
                seg_cd = rv["segundos_cd"]
                log.info(f"  Em CD: {fmt_t(seg_cd)} — dormindo até acabar")
                atualizar_ciclo_file("missao", {
                    "status": "em_cd",
                    "termina_em": (agora() + timedelta(seconds=seg_cd)).isoformat(),
                    "segundos": seg_cd,
                })
                time.sleep(seg_cd + 10)
                continue

            # ── LIVRE: personagem disponível — faz tudo ──────────────────────
            imun = imunidade_restante(estado)
            log.info(f"\n⚡ [AÇÕES] Livre! Imunidade: {fmt_t(imun)}")

            # Atualiza gold real da conta
            try:
                gold_fresh, gems_fresh = parsear_gold_gems(client)
                if gold_fresh > 0:
                    estado["gold_atual"] = gold_fresh
                    salvar_estado(estado)
                elif gold_fresh == 0 and estado.get("gold_atual", 0) > 0:
                    log.debug(f"Gold lido como 0 (estado={estado.get('gold_atual')}g) — aguardando confirmação")
            except Exception:
                pass

            # Scan de lojas (sempre que o personagem está livre)
            if COMPRAR_EQUIPAMENTO:
                try:
                    verificar_alvo_equipamento(client, carregar_estado())
                except Exception as e:
                    log.warning(f"Scan equipamento: erro — {e}")
                try:
                    verificar_alvo_pedra(client, carregar_estado())
                except Exception as e:
                    log.warning(f"Scan pedra: erro — {e}")
                try:
                    verificar_alvo_anel(client, carregar_estado())
                except Exception as e:
                    log.warning(f"Scan anel: erro — {e}")
                try:
                    verificar_alvo_amuleto(client, carregar_estado())
                except Exception as e:
                    log.warning(f"Scan amuleto: erro — {e}")

            # Compras (usa gold do estado atualizado)
            estado = carregar_estado()
            _compras_ciclo = 0
            while COMPRAR_EQUIPAMENTO and _compras_ciclo < 5:
                try:
                    estado = carregar_estado()
                    if not tentar_comprar_item_alvo(client, estado):
                        break
                    _compras_ciclo += 1
                except Exception as e:
                    log.warning(f"Compra item alvo: erro — {e}")
                    break
            try:
                tentar_comprar_pedra(client, carregar_estado())
            except Exception as e:
                log.warning(f"Compra pedra alvo: erro — {e}")
            try:
                tentar_comprar_anel(client, carregar_estado())
            except Exception as e:
                log.warning(f"Compra anel alvo: erro — {e}")
            try:
                tentar_comprar_amuleto(client, carregar_estado())
            except Exception as e:
                log.warning(f"Compra amuleto alvo: erro — {e}")

            # Equipa itens do inventário que ainda não foram equipados
            # e vende duplicatas/piores itens
            if COMPRAR_EQUIPAMENTO:
                try:
                    equipar_melhor_item(client)
                except Exception as e:
                    log.warning(f"Equipar inventário: erro — {e}")
                try:
                    limpar_duplicatas_inventario(client)
                except Exception as e:
                    log.warning(f"Limpar inventário: erro — {e}")

            estado = carregar_estado()

            # LIVRE — decide ação

            # ── Altar: verifica HP atual direto do jogo e reza se < 70% ─────
            try:
                status_fresco = parsear_status(client.get("/status/"))
                hp_atual = status_fresco.get("hp_atual", 0)
                hp_total = status_fresco.get("hp_total", 0)
                # Atualiza estado com HP fresco
                estado_hp = carregar_estado()
                estado_hp.update(status_fresco)
                # Sincroniza imunidade com o servidor
                imun_seg = status_fresco.get("imunidade_seg", 0)
                if imun_seg > 0:
                    novo_ate = (agora() + timedelta(seconds=imun_seg)).isoformat()
                    if estado_hp.get("imunidade_ate") != novo_ate:
                        estado_hp["imunidade_ate"] = novo_ate
                        log.info(f"  Imunidade sincronizada do servidor: {imun_seg//60}min restantes")
                salvar_estado(estado_hp)
                atualizar_ciclo_file("status", status_fresco)

                if hp_total > 0 and hp_atual >= 0:
                    pct_hp = hp_atual / hp_total if hp_total > 0 else 1.0
                    if pct_hp < 0.70:
                        log.info(f"HP baixo ({hp_atual}/{hp_total} = {pct_hp*100:.0f}%) — rezando no altar...")
                        if rezar_altar(client):
                            # Atualiza HP após rezar
                            status_pos = parsear_status(client.get("/status/"))
                            estado_hp.update(status_pos)
                            salvar_estado(estado_hp)
                            atualizar_ciclo_file("status", status_pos)
                            log.info(f"HP após altar: {status_pos.get('hp_atual',0)}/{status_pos.get('hp_total',0)}")
            except Exception as e:
                log.warning(f"Altar: erro ao verificar HP — {e}", exc_info=True)

            # ── Treinamento: gasta gold em atributos disponíveis ──────────────
            try:
                treinados = verificar_treinamento(client)
                if treinados:
                    log.info(f"  Treinamento concluído: {', '.join(treinados)}")
                    # Atualiza gold após gastar em treino
                    gold_pos, _ = parsear_gold_gems(client)
                    if gold_pos > 0:
                        estado["gold_atual"] = gold_pos
                        salvar_estado(estado)
            except Exception as e:
                log.warning(f"Treinamento: erro no loop — {e}")

            pig_list = carregar_pig_list()
            gold_atual = estado.get("gold_atual", 0)
            score_min_imun = SCORE_MIN_IMUNIZACAO  # sempre usa 90% para imunizar
            precisa_imunizar = imun < RENOVAR_IMUNIDADE_SEG
            ataque_feito = False
            imunizou_agora = False  # True se o ataque foi para imunizar (não pig)

            # ── Gold insuficiente para qualquer ataque (< 5g) ─────────────────
            # Ataque PvP custa 5g, missão de campo custa 10g. Taverna é gratuita.
            if gold_atual < 5:
                log.warning(f"⚠ Gold {gold_atual}g < 5g — sem gold para ataques → imunizando e taverna")
                # Tenta imunizar apenas se encontrar alvo (não custa gold, é um ataque mas o custo real é mínimo)
                # Na verdade ataque custa 5g → com < 5g não pode imunizar também
                # Vai direto para taverna para gerar gold
                if TAVERNA_ATIVA:
                    _taverna_1h(client)
                time.sleep(INTERVALO_RAPIDO_SEG)
                continue

            # Tenta atacar pig (confirmados primeiro)
            pigs = sorted(pig_list.items(),
                key=lambda x: (0 if x[1]["categoria"] == "PIG_CONFIRMADO" else 1,
                               x[1].get("tentativas", 0)))

            for uid, pig in pigs:
                pode, motivo = pode_atacar_player(estado, uid)
                if not pode:
                    del pig_list[uid]; salvar_pig_list(pig_list)
                    continue
                log.info(f"  Verificando {pig['nome']} Lv{pig['level']} [{pig['categoria']}]...")
                pig["ultimo_check"] = agora().isoformat()
                pig["tentativas"] = pig.get("tentativas", 0) + 1
                salvar_pig_list(pig_list)

                try:
                    soup = client.get_url(f"{BASE_URL}/player/{uid}/")
                    perfil = parsear_perfil(soup, uid)
                except Exception as e:
                    log.warning(f"    Erro perfil: {e}"); continue

                if not perfil["disponivel"]:
                    log.info(f"    Indisponível (#{pig['tentativas']})"); continue

                # Verifica mesma guild
                meu_clan = estado.get("meu_clan_id")
                if meu_clan and perfil.get("clan_id") == meu_clan:
                    log.info(f"    Pulando: {perfil['nome']} é da mesma guild ({meu_clan})")
                    pig_list.pop(uid, None)
                    salvar_pig_list(pig_list)
                    continue

                av = avaliar_alvo(perfil)
                log.info(f"    Score: {av['score']} → {av['recomendacao']}")

                # Verifica perda de XP esperada
                delta_lv   = MY_STATS["level"] - perfil["level"]
                xp_perda   = max(0, delta_lv - 5)
                gold_esp   = pig.get("gold_esperado", 0)
                gold_conta = estado.get("gold_atual", 0)

                if xp_perda > 0:
                    if gold_esp >= GOLD_IGNORAR_XP:
                        log.info(f"    XP -{xp_perda} ignorado (gold_esp={gold_esp}g >= {GOLD_IGNORAR_XP}g)")
                    elif xp_perda > PERDA_XP_MAX:
                        log.info(f"    Pulando: perderia {xp_perda} XP (max={PERDA_XP_MAX}, gold_esp={gold_esp}g)"); continue

                # Score mínimo para pig
                # Se gold na conta <= 100g, aceita score >= 40 (precisa de qualquer ouro)
                # Caso normal: score >= 60
                # Sem gold: double-check antes de ir para taverna
                if gold_conta == 0:
                    log.warning("⚠ Gold=0 detectado — verificando novamente...")
                    time.sleep(5)
                    gold_real, _ = parsear_gold_gems(client)
                    if gold_real > 0:
                        log.info(f"  Gold OK após double-check: {gold_real}g — continuando")
                        estado["gold_atual"] = gold_real
                        salvar_estado(estado)
                        gold_conta = gold_real
                    else:
                        log.error("⚠ GOLD ZERADO confirmado — tentando aceitar job na taverna...")
                        atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "gold_zerado_taverna"})
                        ok_tab, horas_tab, gold_tab, msg_tab = aceitar_job_taverna(client, horas_max=3)
                        if ok_tab:
                            log.info(f"✓ Taverna: aguardando {horas_tab}h para receber {gold_tab}g")
                            taverna_fim_gz = (agora() + timedelta(hours=horas_tab)).isoformat()
                            atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "taverna", "taverna_fim": taverna_fim_gz, "taverna_horas": horas_tab, "taverna_gold": gold_tab})
                            time.sleep(horas_tab * 3600)
                            atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "ok", "taverna_fim": None})
                            # Sai da taverna acessando a página para concluir o job
                            try:
                                client.get("/job/")
                                log.info("✓ Taverna concluída — retomando bot")
                            except Exception:
                                pass
                            gold_pos, _ = parsear_gold_gems(client)
                            log.info(f"  Gold após taverna: {gold_pos}g")
                        else:
                            log.error(f"✗ Taverna falhou: {msg_tab} — dormindo 1h")
                            time.sleep(3600)
                        continue

                score_pig_min = SCORE_MIN_PIG_BROKE if gold_conta <= GOLD_CONTA_BROKE else SCORE_MIN_PIG
                if av["score"] < score_pig_min:
                    log.info(f"    Score {av['score']} < {score_pig_min} (gold_conta={gold_conta}g) — pulando"); continue

                # Verifica gold mínimo esperado (todos os pigs, inclusive confirmados)
                if gold_esp < GOLD_MIN_PIG:
                    log.info(f"    Gold esperado {gold_esp}g < mínimo {GOLD_MIN_PIG}g — pulando"); continue

                log.info(f"    ✓ ATACANDO {pig['nome']}! (gold_esp={gold_esp}g, xp_perda={xp_perda})")
                meu_clan = estado.get("meu_clan_id")
                ok, score_conf, motivo = verificar_alvo_antes_de_atacar(client, uid, score_pig_min, meu_clan)
                if not ok:
                    log.warning(f"    Ataque cancelado pré-verificação: {motivo}")
                    if motivo == "mesma_guild":
                        pig_list.pop(uid, None); salvar_pig_list(pig_list)
                    continue
                log.info(f"    Score confirmado: {score_conf} — atacando!")
                pig_list[uid]["score_cache"] = score_conf  # salva score para dashboard
                salvar_pig_list(pig_list)
                executar_ataque(client, uid)
                # não remove: executar_ataque já marca como "atacado" com gold/xp/resultado
                ataque_feito = True
                try:
                    treinados = verificar_treinamento(client)
                    if treinados:
                        log.info(f"  Treinamento pós-ataque: {', '.join(treinados)}")
                except Exception as e:
                    log.warning(f"  Treinamento pós-ataque: erro — {e}")
                break

            # Precisa imunizar e não atacou pig?
            if not ataque_feito and precisa_imunizar:
                # Verifica se cache já foi populado
                cache_ok = len(carregar_perfis_cache().get("perfis", {})) > 3
                if not cache_ok:
                    log.info("Cache ainda sendo populado — aguardando para imunizar...")
                else:
                    log.warning(f"⚠ Imunidade expirando em {fmt_t(imun)} — buscando alvo do cache...")
                alvo = buscar_alvo_imunizacao(client, estado, score_min_imun) if cache_ok else None
                # Tenta até 5 alvos diferentes até conseguir imunizar
                alvos_tentados = set()
                for _tentativa_imun in range(5):
                    if not alvo or alvo["user_id"] in alvos_tentados:
                        # Busca próximo alvo excluindo os já tentados
                        alvo = buscar_alvo_imunizacao(client, estado, score_min_imun,
                                                       excluir=alvos_tentados) if cache_ok else None
                    if not alvo:
                        log.warning("Nenhum alvo seguro encontrado no cache!")
                        break

                    alvos_tentados.add(alvo["user_id"])
                    meu_clan = estado.get("meu_clan_id")
                    ok_imun, score_imun, motivo_imun = verificar_alvo_antes_de_atacar(
                        client, alvo["user_id"], 50, meu_clan)
                    if not ok_imun and motivo_imun == "mesma_guild":
                        log.warning(f"  Imunização: {alvo['nome']} mesma guild — próximo...")
                        alvo = None
                        continue
                    if not ok_imun:
                        log.warning(f"  Imunização: {alvo['nome']} indisponível ({motivo_imun}) — próximo...")
                        alvo = None
                        continue

                    log.info(f"Imunizando com {alvo['nome']} Lv{alvo['level']}")
                    res_imun = executar_ataque(client, alvo["user_id"])
                    if res_imun.get("status") == "executado":
                        ataque_feito = True
                        imunizou_agora = True
                        try:
                            treinados = verificar_treinamento(client)
                            if treinados:
                                log.info(f"  Treinamento pós-imunização: {', '.join(treinados)}")
                        except Exception as e:
                            log.warning(f"  Treinamento pós-imunização: erro — {e}")
                        break
                    else:
                        log.warning(f"  Ataque falhou ({res_imun.get('status')}) — próximo alvo...")
                        alvos_tentados.add(alvo["user_id"])  # garante exclusão
                        alvo = None
                        continue  # tenta próximo no loop

            # Nada pra atacar → missão (requer ≥ 10g) ou taverna
            if not ataque_feito:
                if gold_atual < 10:
                    log.info(f"  Gold {gold_atual}g < 10g — não pode iniciar missão de campo → taverna")
                    if TAVERNA_ATIVA:
                        _taverna_1h(client)
                    else:
                        log.info("  Taverna desativada — aguardando próximo ciclo")
                else:
                    res = gerenciar_missao(client)
                    log.info(f"Missão: {res['status']}")
                    if res.get("status") == "iniciada":
                        try:
                            treinados = verificar_treinamento(client)
                            if treinados:
                                log.info(f"  Treinamento pós-missão: {', '.join(treinados)}")
                        except Exception as e:
                            log.warning(f"  Treinamento pós-missão: erro — {e}")

                    # Se missão também indisponível (cota diária ou em CD longo)
                    # → imuniza, entra na taverna 1h, dorme, sai e imuniza de novo
                    if res.get("status") in ("cota_diaria",) or (
                        res.get("status") == "em_cd" and res.get("segundos", 0) > 1800
                    ):
                        if TAVERNA_ATIVA:
                            _taverna_1h(client)
                        else:
                            log.info("  Taverna desativada — aguardando próximo ciclo")
            elif imunizou_agora:
                # Acabou de imunizar — verifica se tem missão disponível (requer ≥ 10g)
                # Se não tiver (ou gold insuficiente), vai para taverna sem esperar CD
                if gold_atual < 10:
                    log.info(f"  Gold {gold_atual}g < 10g após imunizar — sem missão → taverna")
                    if TAVERNA_ATIVA:
                        _taverna_1h(client)
                    else:
                        log.info("  Taverna desativada — aguardando próximo ciclo")
                else:
                    res_check = gerenciar_missao(client)
                    log.info(f"Pós-imunização — Missão: {res_check['status']}")
                    if res_check.get("status") in ("cota_diaria",) or (
                        res_check.get("status") == "em_cd" and res_check.get("segundos", 0) > 1800
                    ):
                        if TAVERNA_ATIVA:
                            log.info("  Sem missão após imunizar — entrando na taverna sem esperar CD")
                            _taverna_1h(client)
                        else:
                            log.info("  Taverna desativada — aguardando próximo ciclo")

        except SessaoExpiradaError as e:
            log.error(f"🔒 COOKIE VENCIDO: {e}")
            novo = renovar_cookie_auto()
            if novo:
                globals()["COOKIES_RAW"] = novo
                client = KFClient(novo)
                atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "ok", "taverna_fim": None})
                log.info("✓ Continuando com novo cookie...")
            else:
                atualizar_ciclo_file("status_bot", {"parado": True, "motivo": "cookie_expirado"})
                log.error("Bot pausado — configure game_user/game_pass no cfg ou atualize o cookie manualmente.")
                time.sleep(3600)
        except Exception as e:
            log.error(f"Erro loop rápido: {e}", exc_info=True)

        time.sleep(INTERVALO_RAPIDO_SEG)

# ═══════════════════════════════════════════
# SERVIDOR DASHBOARD
# ═══════════════════════════════════════════
def iniciar_servidor(porta=8765):
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self):
            if self.path == "/dados":
                self._serve_dados()
            elif self.path == "/log":
                self._serve_file(LOG_FILE, "text/plain; charset=utf-8")
            elif self.path == "/historico":
                combates = carregar_combates_srv()
                self._serve_json(combates[-20:])  # últimos 20
            elif self.path == "/cache":
                self._serve_file(PERFIS_CACHE, "application/json; charset=utf-8")
            elif self.path in ("/", "/dashboard"):
                self._serve_file("dashboard.html", "text/html")
            else:
                self.send_response(404); self.end_headers()

        def do_OPTIONS(self):
            self.send_response(200); self._cors(); self.end_headers()

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET")

        def _serve_dados(self):
            resp = {}
            for fname, key in [(CICLO_FILE, "ciclo"), (ESTADO_FILE, "estado"), (PIG_LIST_FILE, "pig_list")]:
                if os.path.exists(fname):
                    try:
                        with open(fname, encoding="utf-8") as f:
                            resp[key] = json.load(f)
                    except: pass
            if resp.get("ciclo"):
                resp["status"] = resp["ciclo"].get("status", {})
                resp["missao"] = resp["ciclo"].get("missao", {})
                resp["pig_list"] = resp["ciclo"].get("pig_list", resp.get("pig_list", {}))
            # Inclui últimos 20 combates no payload
            try:
                combates = carregar_combates_srv()
                resp["historico"] = combates[-20:]
            except: pass
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._cors(); self.end_headers()
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def _serve_file(self, fname, ctype):
            # Procura o arquivo: primeiro na pasta atual (perfil),
            # depois na pasta do script (raiz do projeto)
            path = fname
            if not os.path.exists(path):
                script_dir = os.path.dirname(os.path.abspath(__file__))
                path = os.path.join(script_dir, fname)
            if os.path.exists(path):
                with open(path, "rb") as f: body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self._cors(); self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404); self.end_headers()

    server = HTTPServer(("localhost", porta), Handler)
    log.info(f"Dashboard: http://localhost:{porta}/dashboard")
    server.serve_forever()

# ═══════════════════════════════════════════
# ENTRYPOINT
# ═══════════════════════════════════════════
if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="KnightFight Bot")
    parser.add_argument("modo", nargs="?", default="loop",
        choices=["loop","status","missao","ranking","cache","pigs","servidor","ciclo"],
        help="Modo de execução")
    parser.add_argument("--profile", "-p",
        help="Nome do perfil (ex: bohrer_int7, alt_int7). Cria pasta profiles/NOME automaticamente.")
    parser.add_argument("--server", help="Servidor (ex: int7, br1, pt2, de3...)")
    parser.add_argument("--cookies", help="Cookie string do browser")
    parser.add_argument("--userid", help="Seu UserID no servidor")
    parser.add_argument("--port", type=int, help="Porta do dashboard (padrão: 8765)")
    parser.add_argument("--workdir", help="Pasta de trabalho explícita (sobrescreve --profile)")
    parser.add_argument("--dry", action="store_true", help="Simula sem executar")
    args = parser.parse_args()

    import os, json as _json

    # ── Resolve pasta de trabalho ─────────────────────────────────────────
    # Prioridade: --workdir > --profile > pasta atual
    workdir = None
    if args.workdir:
        workdir = args.workdir
    elif args.profile:
        workdir = os.path.join("profiles", args.profile)

    if workdir:
        os.makedirs(workdir, exist_ok=True)
        os.chdir(workdir)
        print(f"📁 Perfil: {workdir}")

    # ── Carrega config do perfil se existir ───────────────────────────────
    # Cada pasta pode ter um config.json com server/cookies/userid/port
    cfg = {}
    if os.path.exists("config.json"):
        with open("config.json", encoding="utf-8") as f:
            cfg = _json.load(f)
        print(f"⚙  Configuração carregada de config.json")

    # ── Aplica config (CLI sobrescreve config.json) ───────────────────────
    import sys as _sys
    _mod = _sys.modules[__name__] if __name__ != "__main__" else _sys.modules["__main__"]
    if args.server or cfg.get("server"):
        srv = args.server or cfg["server"]
        globals()["BASE_URL"] = f"https://{srv}.knightfight.moonid.net"
    if args.cookies or cfg.get("cookies"):
        globals()["COOKIES_RAW"] = args.cookies or cfg["cookies"]
    if args.userid or cfg.get("userid"):
        globals()["MY_USER_ID"] = args.userid or cfg["userid"]
    if args.port or cfg.get("port"):
        globals()["DASHBOARD_PORT"] = int(args.port or cfg["port"])

    # Novas configs opcionais
    if cfg.get("ranking_max") is not None:
        globals()["RANKING_MAX_PLAYERS"] = int(cfg["ranking_max"])
    if cfg.get("pausa_cache") is not None:
        globals()["PAUSA_CACHE_SEG"] = float(cfg["pausa_cache"])
    if cfg.get("hora_cache") is not None:
        globals()["HORA_CACHE_PERFIS"] = int(cfg["hora_cache"])
    if cfg.get("score_min_imunizacao") is not None:
        globals()["SCORE_MIN_IMUNIZACAO"] = int(cfg["score_min_imunizacao"])
    if "missao_alinhamento" in cfg:
        globals()["MISSAO_ALINHAMENTO"] = cfg["missao_alinhamento"]
    if "taverna_ativa" in cfg:
        globals()["TAVERNA_ATIVA"] = bool(cfg["taverna_ativa"])
    if "treinar_atributos" in cfg:
        globals()["TREINAR_ATRIBUTOS"] = bool(cfg["treinar_atributos"])
    if "distribuir_skills" in cfg:
        globals()["DISTRIBUIR_SKILLS"] = bool(cfg["distribuir_skills"])
    if "comprar_equipamento" in cfg:
        globals()["COMPRAR_EQUIPAMENTO"] = bool(cfg["comprar_equipamento"])
    if "build_tipo" in cfg:
        globals()["BUILD_TIPO"] = str(cfg["build_tipo"])
        globals()["BUILD_1MAO"] = (str(cfg["build_tipo"]) == "1h")
    elif "build_1mao" in cfg:
        # backward compat: build_1mao antigo sem build_tipo
        globals()["BUILD_1MAO"] = bool(cfg["build_1mao"])
        globals()["BUILD_TIPO"] = "1h" if cfg["build_1mao"] else "2h"
    if "horario_ativo" in cfg:
        globals()["HORARIO_ATIVO"] = bool(cfg["horario_ativo"])
    if "horario_inicio" in cfg:
        globals()["HORARIO_INICIO"] = str(cfg["horario_inicio"])
    if "horario_parada" in cfg:
        globals()["HORARIO_PARADA"] = str(cfg["horario_parada"])
    if cfg.get("score_min_pig") is not None:
        globals()["SCORE_MIN_PIG"]        = int(cfg["score_min_pig"])
    if cfg.get("score_min_pig_broke") is not None:
        globals()["SCORE_MIN_PIG_BROKE"]  = int(cfg["score_min_pig_broke"])
    if cfg.get("gold_conta_broke") is not None:
        globals()["GOLD_CONTA_BROKE"]     = int(cfg["gold_conta_broke"])
    if cfg.get("gold_min_pig") is not None:
        globals()["GOLD_MIN_PIG"]    = int(cfg["gold_min_pig"])
    if cfg.get("perda_xp_max") is not None:
        globals()["PERDA_XP_MAX"]    = int(cfg["perda_xp_max"])
    if cfg.get("gold_ignorar_xp") is not None:
        globals()["GOLD_IGNORAR_XP"] = int(cfg["gold_ignorar_xp"])

    # Auto-login: se não tem cookie mas tem user/pass, faz login automático
    if (COOKIES_RAW == "COLE_SEUS_COOKIES_AQUI" or not COOKIES_RAW.strip()) and cfg.get("game_user") and cfg.get("game_pass"):
        print("🔑 Sem cookie — tentando login automático...")
        try:
            server_auto = cfg.get("server", "int7")
            resultado = fazer_login_moonid(server_auto, cfg["game_user"], cfg["game_pass"])
            novo = resultado["cookie"]
            globals()["COOKIES_RAW"] = novo
            cfg["cookies"] = novo
            # Auto-detecta userid se ainda não está configurado
            if resultado.get("userid") and (not cfg.get("userid") or cfg.get("userid") == "522001100"):
                cfg["userid"] = resultado["userid"]
                globals()["MY_USER_ID"] = resultado["userid"]
                print(f"✓ UserID detectado: {resultado['userid']}")
            with open("config.json", "w", encoding="utf-8") as _f:
                import json as _j2; _j2.dump(cfg, _f, indent=2, ensure_ascii=False)
            print("✓ Login automático OK!")
        except Exception as _e:
            print(f"\n❌ Login automático falhou: {_e}\n")
            sys.exit(1)
    elif COOKIES_RAW == "COLE_SEUS_COOKIES_AQUI":
        print("\n❌ Configure cookies ou game_user/game_pass no config.json\n")
        sys.exit(1)

    client = KFClient(COOKIES_RAW)
    modo = args.modo
    dry  = args.dry

    if modo == "status":
        s = parsear_status(client.get("/status/"))
        print(json.dumps(s, indent=2, ensure_ascii=False))

    elif modo == "missao":
        print(json.dumps(gerenciar_missao(client, dry_run=dry), indent=2, ensure_ascii=False))

    elif modo == "ranking":
        j = scrape_ranking(client)
        salvar_snapshot(j)
        print(f"{len(j)} jogadores salvos")

    elif modo == "cache":
        log.info("Coletando cache de perfis manualmente...")
        # Garante que tem ranking primeiro
        if not carregar_snapshots():
            log.info("Coletando ranking primeiro...")
            j = scrape_ranking(client)
            salvar_snapshot(j)
        coletar_perfis_cache(client)
        print("Cache coletado!")

    elif modo == "pigs":
        pl = carregar_pig_list()
        if not pl: print("Lista vazia")
        for uid, p in pl.items():
            h = seg_desde(p["detectado_em"]) / 3600
            print(f"[{p['categoria']}] {p['nome']:35s} Lv{p['level']} | {h:.1f}h | {p['tentativas']} tentativas")

    elif modo == "servidor":
        iniciar_servidor()

    else:  # loop
        servidor_nome = BASE_URL.replace("https://","").split(".")[0].upper()
        log.info("="*50)
        log.info(f"KnightFight Bot v5 — {servidor_nome} | Dashboard: http://localhost:{DASHBOARD_PORT}/dashboard")
        log.info("="*50)

        # ── Salva info do perfil no ciclo_file para o dashboard ──
        servidor_nome = BASE_URL.replace("https://","").split(".")[0].upper()
        perfil_nome = args.profile or (os.path.basename(os.getcwd()) if args.workdir else "bot")
        atualizar_ciclo_file("perfil", {"nome": perfil_nome, "servidor": servidor_nome})

        # ── Inicia servidor do dashboard imediatamente ──
        # Lê porta do config.json diretamente para garantir valor correto
        _port = DASHBOARD_PORT
        if os.path.exists("config.json"):
            try:
                import json as _j
                _port = int(_j.load(open("config.json", encoding="utf-8")).get("port", DASHBOARD_PORT))
            except: pass
        threading.Thread(target=iniciar_servidor, args=(_port,), daemon=True).start()
        DASHBOARD_PORT = _port

        # ── Coleta status do personagem PRIMEIRO (rápido, 2s) ──
        log.info("Coletando status do personagem...")
        try:
            status = parsear_status(client.get("/status/"))
            atualizar_ciclo_file("status", status)
            gold_conta, gems = parsear_gold_gems(client)
            status["gold_conta"] = gold_conta
            status["gems"] = gems
            atualizar_ciclo_file("status", status)
            estado_atual = carregar_estado()
            estado_atual["gold_atual"] = gold_conta
            estado_atual["gems"] = gems
            salvar_estado(estado_atual)
            log.info(f"Status: Lv{status['level']} | {status['vitorias']}V/{status['derrotas']}D | {gold_conta}g | {gems} pedras")
        except Exception as e:
            log.error(f"Erro status inicial: {e}")

        # ── Verifica se já está em taverna antes de iniciar ──
        try:
            em_taverna, seg_rest = verificar_taverna_ativa(client)
            if em_taverna and seg_rest > 0:
                horas_rest = seg_rest / 3600
                fim_iso = (agora() + timedelta(seconds=seg_rest)).isoformat()
                log.info(f"🍺 Personagem já está em taverna! Restam {fmt_t(seg_rest)} — aguardando...")
                atualizar_ciclo_file("status_bot", {
                    "parado": False, "motivo": "taverna",
                    "taverna_fim": fim_iso,
                    "taverna_horas": round(horas_rest, 1),
                    "taverna_gold": 0
                })
                time.sleep(seg_rest + 10)  # aguarda terminar + 10s folga
                sair_taverna(client)
                atualizar_ciclo_file("status_bot", {"parado": False, "motivo": "ok", "taverna_fim": None})
                log.info("✓ Taverna concluída — imunizando e iniciando bot")
                imunizar_agora(client)
            elif em_taverna:
                log.warning("Personagem em taverna mas sem timer — aguardando 60min por precaução")
                time.sleep(3600)
        except Exception as e:
            log.warning(f"Erro ao verificar taverna inicial: {e}")

        # ── Loop de ações começa AGORA ──────────────────────────────────────
        threading.Thread(target=loop_acoes, args=(client,), daemon=True).start()
        log.info("Loop de ações iniciado — bot já está agindo!")

        # ── Background: skills + scan lojas + ranking inicial ───────────────
        def inicializar_background():
            try:
                distribuir_pontos_skill(client)
            except Exception as e:
                log.warning(f"Skills startup: erro — {e}")
            try:
                verificar_alvo_equipamento(client, carregar_estado())
            except Exception as e:
                log.warning(f"Alvo equipamento startup: erro — {e}")
            try:
                verificar_alvo_pedra(client, carregar_estado())
            except Exception as e:
                log.warning(f"Alvo pedra startup: erro — {e}")
            try:
                verificar_alvo_anel(client, carregar_estado())
            except Exception as e:
                log.warning(f"Alvo anel startup: erro — {e}")
            try:
                verificar_alvo_amuleto(client, carregar_estado())
            except Exception as e:
                log.warning(f"Alvo amuleto startup: erro — {e}")

            log.info("Background: coletando ranking inicial...")
            try:
                j = scrape_ranking(client)
                salvar_snapshot(j)
                log.info("Background: ranking coletado!")
            except Exception as e:
                log.error(f"Erro ranking inicial: {e}")

            if cache_precisa_atualizar():
                log.info("Background: coletando cache de perfis (~15min)...")
                try:
                    coletar_perfis_cache(client)
                    log.info("Background: cache de perfis concluído!")
                except Exception as e:
                    log.error(f"Erro cache inicial: {e}")

        threading.Thread(target=inicializar_background, daemon=True).start()

        # ── Loop ranking (1h): ranking + pig list, independente do estado do personagem ──
        threading.Thread(target=loop_ranking, args=(client,), daemon=True).start()

        # ── Loop lento (1h): status + cache de perfis ────────────────────────
        threading.Thread(target=loop_lento, args=(client,), daemon=True).start()

        log.info("Bot rodando. Ctrl+C para parar.")
        try:
            while True: time.sleep(60)
        except KeyboardInterrupt:
            log.info("Bot encerrado")
