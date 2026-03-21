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
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional

# ═══════════════════════════════════════════
# CONFIGURAÇÃO
# ═══════════════════════════════════════════
# ── Config padrão (pode ser sobrescrita por argumentos CLI) ──────────────
BASE_URL    = "https://int7.knightfight.moonid.net"
COOKIES_RAW = "COLE_SEUS_COOKIES_AQUI"
MY_USER_ID  = "522001100"
DASHBOARD_PORT = 8765

MY_STATS = {
    "level": 22, "forca": 51, "resistencia": 51,
    "agilidade": 5, "arte_combate": 71, "bloqueio": 71,
    "skill_2maos": 66, "dano_min": 58, "dano_max": 63, "hp": 1540,
}

IS_PREMIUM = False

def level_min_xp():
    """Calcula dinamicamente para acompanhar o level atual."""
    return MY_STATS["level"] - 5

COOLDOWN_ATAQUE_SEG   = 300 if IS_PREMIUM else 900
IMUNIDADE_SEG         = 3600
BLOQUEIO_MESMO_PLAYER = 43200
RENOVAR_IMUNIDADE_SEG = 600

HORAS_MISSAO_DIA  = 2 if IS_PREMIUM else 1

SCORE_MIN_PIG        = 40
SCORE_MIN_IMUNIZACAO = 60
SCORE_MIN_GOLD_ALTO  = 75
GOLD_ALTO_THRESHOLD  = 5000

INTERVALO_RAPIDO_SEG = 120
INTERVALO_LENTO_SEG  = 3600
HORA_CACHE_PERFIS    = 3      # 3h da manhã
PAUSA_CACHE_SEG      = 2.0   # pausa entre perfis (mais seguro)

RANKING_FILE  = "ranking_snapshots.json"
PIG_LIST_FILE = "pig_list.json"
PERFIS_CACHE  = "perfis_cache.json"
ESTADO_FILE   = "estado.json"
CICLO_FILE    = "ultimo_ciclo.json"
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("KFBot")

# ═══════════════════════════════════════════
# UTILS
# ═══════════════════════════════════════════
def parse_num(txt):
    c = re.sub(r"[^\d]", "", str(txt))
    return int(c) if c else 0

def agora():
    return datetime.now()

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
def carregar_estado():
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
    with open(ESTADO_FILE, "w", encoding="utf-8") as f:
        json.dump(e, f, indent=2, ensure_ascii=False)

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
        return BeautifulSoup(r.text, "html.parser")

    def get_url(self, url):
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
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
        with open(PERFIS_CACHE, encoding="utf-8") as f:
            return json.load(f)
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
            perfis[uid] = {
                **perfil,
                "nome": j["nome"],
                "level": perfil["level"] or j["level"],
                "win_rate_ranking": round(j["vitorias"] / j["combates"] * 100, 1) if j.get("combates", 0) > 0 else 50,
                "coletado_em": agora().isoformat(),
            }
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
        "hp": hp, "disponivel": disponivel,
    }

def avaliar_alvo(perfil, eu=None):
    """
    Score 0-100 para chance de vitória.

    Fórmula central:
      taxa_meu_ataque  = minha_AC / (minha_AC + bloqueio_dele)
      taxa_ataque_dele = AC_dele  / (AC_dele  + meu_bloqueio)

    Resistência alta = mais rounds = risco de virada mesmo sendo mais fraco.
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

    blq = perfil.get("bloqueio", 0)
    ac_d = perfil.get("arte_combate", 0)
    res_d = perfil.get("resistencia", 0)
    frc_d = perfil.get("forca", 0)
    lv_d = perfil.get("level", 0)

    # 1. Minha taxa de acerto
    if blq > 0:
        taxa = minha_ac / (minha_ac + blq)
        if taxa < 0.35:
            problemas.append(f"AC {minha_ac} vs bloqueio {blq} → {taxa*100:.0f}% — ruim")
            score -= 40
        elif taxa < 0.50:
            problemas.append(f"AC {minha_ac} vs bloqueio {blq} → {taxa*100:.0f}% — baixo")
            score -= 20
        else:
            vantagens.append(f"AC {minha_ac} vs bloqueio {blq} → {taxa*100:.0f}% ✓")
            score += 20

    # 2. Taxa de acerto dele
    if ac_d > 0:
        taxa_d = ac_d / (ac_d + meu_blq)
        if taxa_d > 0.65:
            problemas.append(f"AC dele {ac_d} vs meu bloqueio {meu_blq} → {taxa_d*100:.0f}%")
            score -= 20
        elif taxa_d < 0.45:
            vantagens.append(f"Meu bloqueio segura {(1-taxa_d)*100:.0f}% dos hits ✓")
            score += 15

    # 3. Resistência (rounds)
    if res_d > 200:
        problemas.append(f"Resistência {res_d} — risco de virada por rounds")
        score -= 15
    elif res_d > 0 and res_d < minha_res * 0.7:
        vantagens.append(f"Resistência baixa {res_d} ✓")
        score += 10

    # 4. Força
    if frc_d > minha_frc * 1.8:
        problemas.append(f"Força {frc_d} >> minha {minha_frc}")
        score -= 15
    elif frc_d > 0 and frc_d < minha_frc * 0.6:
        vantagens.append(f"Força {frc_d} << minha {minha_frc} ✓")
        score += 10

    # 5. Level
    delta_lv = lv_d - meu_lv
    if delta_lv > 10:
        problemas.append(f"Level muito superior {lv_d} vs {meu_lv}")
        score -= 20
    elif delta_lv > 5:
        problemas.append(f"Level superior {lv_d} vs {meu_lv}")
        score -= 8
    elif delta_lv < -5:
        vantagens.append(f"Level inferior {lv_d} vs {meu_lv} ✓")
        score += 5

    score = max(0, min(100, score))
    rec = "ATACAR" if score >= 60 else ("CUIDADO" if score >= 40 else "EVITAR")
    return {"recomendacao": rec, "score": score,
            "vantagens": vantagens, "problemas": problemas}

def parsear_resultado_combate(soup, eu_fui_atacante=True):
    """
    Extrai resultado do relatório de combate.

    O resultado está em dois lugares:
    1. JSON do displayFightReport: "winner": "attacker" ou "defender"
       - Se eu ataquei (eu_fui_atacante=True): winner=attacker → vitória
       - Se fui atacado (eu_fui_atacante=False): winner=defender → vitória
    2. HTML: gold e XP ganhos ficam como "238 [img gold_coin]"
    """
    resultado = "desconhecido"
    gold_ganho = 0
    xp_ganho = 0

    # 1. Extrai winner do JSON do displayFightReport
    for script in soup.find_all("script"):
        txt = script.string or ""
        if "displayFightReport" not in txt:
            continue
        m = re.search(r'"winner"\s*:\s*"(\w+)"', txt)
        if m:
            winner = m.group(1)  # "attacker" ou "defender"
            if eu_fui_atacante:
                resultado = "vitoria" if winner == "attacker" else "derrota"
            else:
                resultado = "vitoria" if winner == "defender" else "derrota"
        break

    # 2. Extrai gold e XP do HTML
    # Padrão: "238 [img gold_coin]" → procura número antes da imagem gold_coin
    html_txt = str(soup)
    m_gold = re.search(r"(\d+)\s*<img[^>]*gold_coin[^>]*>", html_txt)
    if m_gold:
        gold_ganho = int(m_gold.group(1))

    # XP: número antes de exp_scroll
    m_xp = re.findall(r"(\d+)\s*<img[^>]*exp_scroll[^>]*>", html_txt)
    # Pega o XP do atacante (primeiro valor, geralmente maior que 0)
    for v in m_xp:
        if int(v) > 0:
            xp_ganho = int(v)
            break

    return resultado, gold_ganho, xp_ganho


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
        log.warning(f"displayFightReport ausente — combate não ocorreu para {user_id}")
        return {"status": "indisponivel", "user_id": user_id}

    resultado, gold_ganho, xp_ganho = parsear_resultado_combate(soup_result, eu_fui_atacante=True)

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
def buscar_alvo_imunizacao(client, estado, score_min):
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

    log.info(f"Candidatos imunização no cache: {len(candidatos)} (score_min={score_min})")

    # Filtra por score mínimo
    validos = [c for c in candidatos if c["score"] >= score_min]
    log.info(f"  Com score >= {score_min}: {len(validos)}")

    if not validos:
        # Relaxa o score se não encontrou ninguém
        score_relaxado = max(score_min - 15, 30)
        validos = [c for c in candidatos if c["score"] >= score_relaxado]
        log.info(f"  Score relaxado para {score_relaxado}: {len(validos)} candidatos")

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
        paginas = [100, 200, 300, 400, 500]
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
        with open(RANKING_FILE, encoding="utf-8") as f:
            snapshots = json.load(f)
    snapshots.append({"timestamp": agora().isoformat(), "jogadores": jogadores})
    snapshots = snapshots[-50:]
    with open(RANKING_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshots, f, indent=2, ensure_ascii=False)

def carregar_snapshots():
    if not os.path.exists(RANKING_FILE): return []
    with open(RANKING_FILE, encoding="utf-8") as f:
        return json.load(f)

# ═══════════════════════════════════════════
# PIG LIST
# ═══════════════════════════════════════════
def carregar_pig_list():
    if os.path.exists(PIG_LIST_FILE):
        with open(PIG_LIST_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def salvar_pig_list(pig_list):
    with open(PIG_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(pig_list, f, indent=2, ensure_ascii=False)

def atualizar_pig_list(pig_list, jogadores_ant, jogadores_atu, estado):
    """
    Regras de adição:
      PIG_CONFIRMADO: ouro_perdido++ OU derrotas++ entre snapshots
      PIG_PROVAVEL:   preciosidades++ (terminou missão, tem gold)

    Regras de remoção:
      - Está no histórico (eu já ataquei nas últimas 12h) → move para histórico, não remove
      - derrotas++ MAS ouro_perdido == 0 na hora seguinte →
        alguém atacou mas não ganhou gold → provavelmente zerou, remove
      - NÃO remove por tempo (fica até confirmar que zerou ou até eu atacar)

    Histórico (pig_list[uid]["status"] == "atacado"):
      - Mantém por 12h após o ataque
      - Após 12h: se ainda aparecer como pig → volta para ativo
      - Após 12h: se não aparecer mais → remove do histórico
    """
    agora_iso = agora().isoformat()
    adicionados = removidos = 0
    hist_ataques = estado.get("historico_ataques", {})

    for uid, j in jogadores_atu.items():
        if uid == MY_USER_ID:
            continue
        if uid not in jogadores_ant:
            continue

        a = jogadores_ant[uid]
        dd    = j["derrotas"]      - a["derrotas"]
        dp    = j["ouro_perdido"]  - a["ouro_perdido"]
        dprec = j["preciosidades"] - a["preciosidades"]
        wr = round(j["vitorias"] / j["combates"] * 100, 1) if j.get("combates", 0) > 0 else 0

        # Verifica se está no histórico de ataques meus
        meu_ataque_iso = hist_ataques.get(uid)
        eu_ataquei_recente = meu_ataque_iso and seg_desde(meu_ataque_iso) < BLOQUEIO_MESMO_PLAYER

        base = {
            "nome": j["nome"], "user_id": uid, "level": j["level"], "win_rate": wr,
            "preciosidades": j["preciosidades"],
            "ouro_ganho": j["ouro_ganho"], "ouro_perdido": j["ouro_perdido"],
            "detectado_em": pig_list.get(uid, {}).get("detectado_em", agora_iso),
            "tentativas": pig_list.get(uid, {}).get("tentativas", 0),
            "ultimo_check": pig_list.get(uid, {}).get("ultimo_check"),
        }

        # Pig que eu ataquei recentemente → marca como histórico
        if eu_ataquei_recente:
            if uid in pig_list and pig_list[uid].get("status") != "atacado":
                pig_list[uid]["status"] = "atacado"
                pig_list[uid]["atacado_em"] = meu_ataque_iso
            elif uid not in pig_list and (dp > 0 or dd > 0 or dprec > 0):
                base["status"] = "atacado"
                base["atacado_em"] = meu_ataque_iso
                base["categoria"] = "PIG_CONFIRMADO" if dp > 0 or dd > 0 else "PIG_PROVAVEL"
                base["motivos"] = []
                pig_list[uid] = base
            continue

        # Após 12h do meu ataque: decide se volta pra ativo ou sai
        if uid in pig_list and pig_list[uid].get("status") == "atacado":
            h_desde_ataque = seg_desde(pig_list[uid].get("atacado_em", agora_iso)) / 3600
            if h_desde_ataque >= 12:
                if dp > 0 or dd > 0 or dprec > 0:
                    # Voltou a ser pig → reativa
                    pig_list[uid]["status"] = "ativo"
                    pig_list[uid]["detectado_em"] = agora_iso
                    log.info(f"  ↩ REATIVADO: {j['nome']} (passou 12h do ataque, novo sinal)")
                else:
                    # Não é mais pig → remove do histórico
                    del pig_list[uid]
                    removidos += 1
            continue

        # Lógica de remoção por "zerou":
        # Estava na lista como confirmado, nova hora: derrotas++ mas ouro_perdido == 0
        # Significa que foi atacado mas não tinha gold → removido
        if uid in pig_list and pig_list[uid].get("status","ativo") == "ativo":
            if pig_list[uid].get("categoria") == "PIG_CONFIRMADO":
                ouro_ref = pig_list[uid].get("ouro_perdido", 0)
                if dd > 0 and j["ouro_perdido"] == ouro_ref:
                    log.info(f"  - ZEROU: {j['nome']} (tomou derrota mas ouro_perdido={j['ouro_perdido']} não subiu)")
                    del pig_list[uid]
                    removidos += 1
                    continue

        # Adiciona/atualiza pigs
        if dp > 0 or dd > 0:
            motivos = ([f"+{dd} derrota(s)"] if dd > 0 else []) + ([f"+{dp} ouro perdido"] if dp > 0 else [])
            if uid not in pig_list:
                base["categoria"] = "PIG_CONFIRMADO"
                base["motivos"] = motivos
                base["status"] = "ativo"
                base["delta_ouro_perdido"] = dp
                base["delta_derrotas"] = dd
                pig_list[uid] = base
                adicionados += 1
                log.info(f"  + CONFIRMADO: {j['nome']} Lv{j['level']} | {', '.join(motivos)}")
            else:
                # Atualiza delta acumulado
                pig_list[uid]["delta_ouro_perdido"] = pig_list[uid].get("delta_ouro_perdido", 0) + dp
                pig_list[uid]["delta_derrotas"] = pig_list[uid].get("delta_derrotas", 0) + dd
                pig_list[uid]["motivos"] = motivos
        elif dprec > 0 and uid not in pig_list:
            base["categoria"] = "PIG_PROVAVEL"
            base["motivos"] = [f"+{dprec} preciosidades (terminou missão)"]
            base["status"] = "ativo"
            base["delta_prec"] = dprec
            base["delta_ouro_perdido"] = 0
            pig_list[uid] = base
            adicionados += 1
            log.info(f"  + PROVÁVEL: {j['nome']} Lv{j['level']} | +{dprec} prec")

    log.info(f"Pig list: {len(pig_list)} total | +{adicionados} adicionados | -{removidos} removidos")
    return pig_list

# ═══════════════════════════════════════════
# RAUBZUG — estado de CD
# ═══════════════════════════════════════════
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

    m2 = re.search(r"Already used:\s*(\d+)\s*minutes", txt)
    if m2:
        resultado["minutos_usados_hoje"] = int(m2.group(1))

    for form in soup.find_all("form"):
        if form.find("input", {"name": "ac", "value": "raubzug"}) and \
           form.find("input", {"name": "sac", "value": "mission"}):
            inp = form.find("input", {"name": "csrftoken"})
            if inp: resultado["csrf_missao"] = inp.get("value", "")
            break

    return resultado

# ═══════════════════════════════════════════
# MISSÕES
# ═══════════════════════════════════════════
def gerenciar_missao(client, dry_run=False):
    estado = carregar_estado()
    limite_min = 120 if IS_PREMIUM else 60

    rv = verificar_raubzug(client)

    if not rv["livre"]:
        fim = agora() + timedelta(seconds=rv["segundos_cd"])
        log.info(f"Em CD — livre às {fim:%H:%M:%S}")
        return {"status": "em_cd", "termina_em": fim.isoformat(), "segundos": rv["segundos_cd"]}

    minutos_usados = rv["minutos_usados_hoje"] or estado.get("minutos_missao_hoje", 0)
    minutos_rest = limite_min - minutos_usados

    if minutos_rest <= 0:
        log.info(f"Cota diária atingida ({limite_min}min)")
        return {"status": "cota_diaria", "minutos_usados": minutos_usados}

    jagdzeit = 10
    gesinnung = "1" if estado.get("missoes_hoje", 0) % 2 == 0 else "2"
    log.info(f"Missão: {jagdzeit}min | {'bem' if gesinnung=='1' else 'mal'} | usados={minutos_usados}/{limite_min}min")

    if dry_run:
        return {"status": "dry_run", "jagdzeit": jagdzeit, "minutos_rest": minutos_rest}

    csrf = rv["csrf_missao"]
    data = {"csrftoken": csrf, "ac": "raubzug", "sac": "mission",
            "gesinnung": gesinnung, "jagdzeit": str(jagdzeit)}
    r = client.session.post(BASE_URL + "/", data=data, timeout=15)
    if r.status_code == 403:
        log.warning("403 — recarregando CSRF...")
        rv2 = verificar_raubzug(client)
        data["csrftoken"] = rv2["csrf_missao"]
        r = client.session.post(BASE_URL + "/", data=data, timeout=15)
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
        Tooltips do status próprio: "Strength: 51 + 2" ou "Parry: 71"
        Tooltips do perfil alheio: "Arte de combate: (172)"
        Pega sempre o PRIMEIRO número após o separador.
        """
        for tag in soup.find_all(attrs={"data-tooltip": True}):
            tip = tag["data-tooltip"]
            for n in nomes:
                if n.lower() in tip.lower():
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
def loop_lento(client):
    """A cada 1h: ranking + pig list + status. Às 3h: cache de perfis."""
    while True:
        log.info("\n🔄 [LENTO] Iniciando ciclo horário...")
        try:
            estado = carregar_estado()

            # Status do personagem → dashboard
            try:
                status = parsear_status(client.get("/status/"))
                atualizar_ciclo_file("status", status)
                log.info(f"Status: Lv{status['level']} | {status['vitorias']}V/{status['derrotas']}D | {status['preciosidades']} prec")
            except Exception as e:
                log.error(f"Erro status: {e}")

            # Cache de perfis às 3h (ou se expirou)
            hora_atual = agora().hour
            if hora_atual == HORA_CACHE_PERFIS and cache_precisa_atualizar():
                log.info("3h da manhã — atualizando cache de perfis...")
                coletar_perfis_cache(client)

            # Ranking + pig list
            jogadores = scrape_ranking(client)
            if jogadores:
                salvar_snapshot(jogadores)
                snaps = carregar_snapshots()
                if len(snaps) >= 2:
                    pig_list = carregar_pig_list()
                    pig_list = atualizar_pig_list(pig_list, snaps[-2]["jogadores"], snaps[-1]["jogadores"], estado)
                    salvar_pig_list(pig_list)
                    atualizar_ciclo_file("pig_list", pig_list)
                else:
                    log.info("Aguardando 2º snapshot para comparar (próxima hora)")

        except Exception as e:
            log.error(f"Erro loop lento: {e}", exc_info=True)

        time.sleep(INTERVALO_LENTO_SEG)


def loop_rapido(client):
    """
    A cada 2min: verifica CD → ataca pig / imuniza / faz missão.
    REGRA: missão e ataque compartilham o mesmo CD. Nunca os dois juntos.
    """
    while True:
        try:
            estado = carregar_estado()
            imun = imunidade_restante(estado)
            log.info(f"\n⚡ [RÁPIDO] Imunidade: {fmt_t(imun)}")

            rv = verificar_raubzug(client)
            atualizar_ciclo_file("estado", estado)

            if not rv["livre"]:
                log.info(f"  Em CD: {fmt_t(rv['segundos_cd'])} restantes")
                # Atualiza missão no dashboard com o CD real
                atualizar_ciclo_file("missao", {
                    "status": "em_cd",
                    "termina_em": (agora() + timedelta(seconds=rv["segundos_cd"])).isoformat(),
                    "segundos": rv["segundos_cd"],
                })
                time.sleep(min(rv["segundos_cd"], INTERVALO_RAPIDO_SEG))
                continue

            # LIVRE — decide ação
            pig_list = carregar_pig_list()
            gold_atual = estado.get("gold_atual", 0)
            score_min_imun = SCORE_MIN_GOLD_ALTO if gold_atual > GOLD_ALTO_THRESHOLD else SCORE_MIN_IMUNIZACAO
            precisa_imunizar = imun < RENOVAR_IMUNIDADE_SEG
            ataque_feito = False

            # Tenta atacar pig (confirmados primeiro)
            pigs = sorted(pig_list.items(),
                key=lambda x: (0 if x[1]["categoria"] == "PIG_CONFIRMADO" else 1,
                               x[1].get("tentativas", 0)))

            for uid, pig in pigs:
                pode, motivo = pode_atacar_player(estado, uid)
                if not pode:
                    del pig_list[uid]; salvar_pig_list(pig_list)
                    continue
                if pig.get("ultimo_check") and seg_desde(pig["ultimo_check"]) < 60:
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

                av = avaliar_alvo(perfil)
                log.info(f"    Score: {av['score']} → {av['recomendacao']}")

                if perfil["level"] < level_min_xp():
                    if not (pig["categoria"] == "PIG_CONFIRMADO" and av["score"] >= SCORE_MIN_PIG):
                        log.info(f"    Lv{perfil['level']} < {level_min_xp()} — pulando"); continue

                if av["score"] < SCORE_MIN_PIG:
                    log.info(f"    Score insuficiente ({av['score']} < {SCORE_MIN_PIG})"); continue

                log.info(f"    ✓ ATACANDO {pig['nome']}!")
                executar_ataque(client, uid)
                pig_list.pop(uid, None); salvar_pig_list(pig_list)
                ataque_feito = True
                break

            # Precisa imunizar e não atacou pig?
            if not ataque_feito and precisa_imunizar:
                log.warning(f"⚠ Imunidade expirando em {fmt_t(imun)} — buscando alvo do cache...")
                alvo = buscar_alvo_imunizacao(client, estado, score_min_imun)
                if alvo:
                    log.info(f"Imunizando com {alvo['nome']} Lv{alvo['level']}")
                    executar_ataque(client, alvo["user_id"])
                    ataque_feito = True
                else:
                    log.warning("Nenhum alvo seguro encontrado no cache!")

            # Nada pra atacar → missão
            if not ataque_feito:
                res = gerenciar_missao(client)
                log.info(f"Missão: {res['status']}")

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
            body = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._cors(); self.end_headers()
            self.wfile.write(body)

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
    parser.add_argument("--server", choices=["int7","int6","int5","int4","int3","int2","int1"],
        help="Servidor (ex: int6). Se --profile existir no profiles.json, lido de lá.")
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
    global BASE_URL, COOKIES_RAW, MY_USER_ID, DASHBOARD_PORT
    if args.server or cfg.get("server"):
        srv = args.server or cfg["server"]
        BASE_URL = f"https://{srv}.knightfight.moonid.net"
    if args.cookies or cfg.get("cookies"):
        COOKIES_RAW = args.cookies or cfg["cookies"]
    if args.userid or cfg.get("userid"):
        MY_USER_ID = args.userid or cfg["userid"]
    if args.port or cfg.get("port"):
        DASHBOARD_PORT = int(args.port or cfg["port"])

    if COOKIES_RAW == "COLE_SEUS_COOKIES_AQUI":
        print("\n❌ Configure COOKIES_RAW no bot.py ou passe --cookies 'seu_cookie'\n")
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

        # Coleta ranking inicial
        log.info("Coletando ranking inicial...")
        try:
            j = scrape_ranking(client)
            salvar_snapshot(j)
        except Exception as e:
            log.error(f"Erro ranking inicial: {e}")

        # Coleta cache de perfis ao iniciar (sempre)
        log.info("Coletando cache de perfis (primeira vez ou expirado)...")
        try:
            coletar_perfis_cache(client)
        except Exception as e:
            log.error(f"Erro cache inicial: {e}")

        # Inicia threads
        threading.Thread(target=iniciar_servidor, args=(DASHBOARD_PORT,), daemon=True).start()
        threading.Thread(target=loop_lento, args=(client,), daemon=True).start()
        threading.Thread(target=loop_rapido, args=(client,), daemon=True).start()

        log.info("Bot rodando. Ctrl+C para parar.")
        try:
            while True: time.sleep(60)
        except KeyboardInterrupt:
            log.info("Bot encerrado")
