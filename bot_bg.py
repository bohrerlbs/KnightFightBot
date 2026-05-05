# ═══════════════════════════════════════════════════════════════
# KnightFight — BattleGround Bot v1.0.6
# Bot separado para o Battleground (BG)
# ═══════════════════════════════════════════════════════════════
import os, sys, json, time, re, logging, argparse, threading
from datetime import datetime, timedelta
from pathlib import Path
from bs4 import BeautifulSoup
import requests

# ── Encoding UTF-8 no Windows ───────────────────────────────────
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8')
    except: pass

# ═══════════════════════════════════════════════════════════════
# CONFIGURAÇÃO — editada pelo launcher via config_bg.json
# ═══════════════════════════════════════════════════════════════
BASE_URL       = "https://int7.knightfight.moonid.net"
COOKIES_RAW    = "COLE_SEUS_COOKIES_AQUI"
MY_USER_ID     = "522001100"
DASHBOARD_PORT = 8770

# Modos BG
MODOS_BG = {
    "free":    {"batalhas": 100, "cooldown": 600,  "nome": "Free (100 batalhas)"},
    "medio":   {"batalhas": 200, "cooldown": 300,  "nome": "150 pedras (200 batalhas)"},
    "premium": {"batalhas": 400, "cooldown": 300,  "nome": "250 pedras (400 batalhas)"},
}
MODO_BG = "free"  # auto-detectado pela sessão

def detectar_modo_bg(sessao):
    """Auto-detecta o modo BG pelo total de batalhas da sessão."""
    total = sessao.get("batalhas_total", 100)
    if total >= 400:
        return "premium"
    elif total >= 200:
        return "medio"
    return "free"

# Estratégia de busca
EF_RANGE_ACIMA   = 2.0   # busca EF nossa até +2.0
EF_RANGE_FALLBACK = 1.0  # se não achar bom, tenta com +1.0
SCORE_MIN_ATACAR = 60    # score mínimo para atacar (simulador preciso)
MAX_REBUSCAS     = 3     # tentativas de busca antes de desistir do ciclo
EF_OFFSET_MAX    = 5.0   # começa buscando em minha_EF + offset e vai descendo até minha_EF

# ── Arquivos de estado ──────────────────────────────────────────
WORKDIR        = Path(".")
SCRIPT_DIR  = Path(__file__).parent.resolve()
STATE_FILE     = WORKDIR / "bg_estado.json"
COMBATES_FILE  = WORKDIR / "bg_combates.json"
CICLO_FILE     = WORKDIR / "bg_ciclo.json"
LOG_FILE       = WORKDIR / "bot_bg.log"

# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════
log = logging.getLogger("bg_bot")
log.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_sh)

def init_log_file():
    from logging.handlers import RotatingFileHandler
    fh = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2 * 1024 * 1024,  # 2 MB por arquivo
        backupCount=2,
        encoding="utf-8",
        delay=True,
    )
    fh.setFormatter(_fmt)
    log.addHandler(fh)
    # Limpa logs antigos (>48h)
    try:
        import glob as _g, time as _t
        for f in _g.glob(str(LOG_FILE) + ".*"):
            if _t.time() - os.path.getmtime(f) > 48 * 3600:
                os.remove(f)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
# UTILITÁRIOS
# ═══════════════════════════════════════════════════════════════
def agora():
    return datetime.now()

def fmt_t(seg):
    seg = max(0, int(seg))
    h, r = divmod(seg, 3600)
    m, s = divmod(r, 60)
    if h: return f"{h:02d}:{m:02d}h"
    return f"{m:02d}:{s:02d}"

def carregar_json(path, default):
    try:
        if Path(path).exists():
            return json.loads(Path(path).read_text(encoding="utf-8"))
    except: pass
    return default

def salvar_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def carregar_estado():
    return carregar_json(STATE_FILE, {})

def salvar_estado(d):
    salvar_json(STATE_FILE, d)

def carregar_combates():
    return carregar_json(COMBATES_FILE, [])

def salvar_combates(d):
    salvar_json(COMBATES_FILE, d)

def resetar_dados_sessao_bg():
    """
    Reseta dados da sessão anterior do BG para nova sessão.
    - Apaga bg_combates.json (histórico de combates)
    - Apaga bg_ciclo.json (dados do dashboard)
    - NO estado: reseta contadores mas preserva flags de controle (parar_bot etc)
    """
    try:
        if COMBATES_FILE.exists():
            COMBATES_FILE.unlink()
            log.info("✓ Histórico de combates BG resetado")
        if CICLO_FILE.exists():
            CICLO_FILE.unlink()
            log.info("✓ Ciclo BG resetado")
        # Reseta contadores no estado mas preserva flags de controle
        if STATE_FILE.exists():
            est = carregar_json(STATE_FILE, {})
            est["batalhas_feitas"] = 0
            est["vitorias"]   = 0
            est["derrotas"]   = 0
            est["gold_total"] = 0
            est["xp_total"]   = 0
            est.pop("sessao_bg_id", None)
            est.pop("sessao_bg", None)
            salvar_json(STATE_FILE, est)
            log.info("✓ Contadores de estado BG resetados")
    except Exception as e:
        log.warning(f"Erro ao resetar dados BG: {e}")

def atualizar_ciclo(chave, valor):
    ciclo = carregar_json(CICLO_FILE, {})
    ciclo[chave] = valor
    ciclo["atualizado_em"] = agora().isoformat()
    salvar_json(CICLO_FILE, ciclo)

# ═══════════════════════════════════════════════════════════════
# CLIENTE HTTP
# ═══════════════════════════════════════════════════════════════
class ClienteBG:
    def __init__(self, base_url, cookies_raw):
        self.base_url = base_url.rstrip("/")
        self.bs_url   = self.base_url + "/battleserver"
        log.info(f"ClienteBG: {self.bs_url}")
        if not cookies_raw or cookies_raw == "COLE_SEUS_COOKIES_AQUI":
            log.error("COOKIES NAO CONFIGURADOS! Configure no launcher.")
            raise ValueError("Cookies não configurados")
        self.session  = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        })
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Referer": self.bs_url + "/",
        })
        # Parse cookies
        for part in cookies_raw.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                self.session.cookies.set(k.strip(), v.strip())

    def get(self, path, fragment=True):
        url = self.bs_url + path
        if fragment:
            url += ("&" if "?" in path else "?") + "fragment=1"
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")

    def get_full(self, path):
        return self.get(path, fragment=False)

    def post(self, data, referer=None, fragment=True):
        # BG usa POST para battleserver/?fragment=1
        url = self.bs_url + "/?fragment=1" if fragment else self.bs_url + "/"
        headers = {
            "Referer":          referer or (self.bs_url + "/raubzug/"),
            "Origin":           self.base_url,
            "X-Requested-With": "XMLHttpRequest",
            "Accept":           "text/html, */*; q=0.01",
        }
        r = self.session.post(url, data=data, headers=headers, timeout=15)
        if r.status_code == 418:
            # Servidor detectou automação — espera antes de tentar de novo
            log.warning(f"418 I'm a teapot — servidor bloqueou temporariamente. Aguardando 5min...")
            time.sleep(300)
            r = self.session.post(url, data=data, headers=headers, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")

    def get_main(self, path):
        """GET no site principal (não no battleserver)."""
        url = self.base_url + path
        r = self.session.get(url, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")

    def post_main(self, path, data, referer=None):
        """POST no site principal (não no battleserver)."""
        url = self.base_url + path
        headers = {
            "Referer": referer or (self.base_url + "/battleground/"),
            "Origin":  self.base_url,
            "Accept":  "text/html,application/xhtml+xml,*/*",
        }
        r = self.session.post(url, data=data, headers=headers, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")

# ═══════════════════════════════════════════════════════════════
# PARSERS
# ═══════════════════════════════════════════════════════════════
def _clan_id_de_perfil_bg(soup):
    """Extrai clan_id do perfil adversário no BG."""
    for tag in soup.find_all("a", href=True):
        m = re.search(r"/clan/(\d+)/", tag["href"])
        if m:
            return int(m.group(1))
    return None


def get_my_clan_id_bg(client):
    """Lê meu clan_id."""
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


def parsear_status_bg(soup):
    """Extrai stats do personagem na página do BG."""
    status = {}
    try:
        # EF: div.be > span.tooltip com data-tooltip="Eficiência em batalha: 2,4 (= ~2,5)"
        be_div = soup.find("div", class_="be")
        if be_div:
            span = be_div.find("span", class_="tooltip")
            if span:
                # Pega o valor visível (ex: "2,5")
                txt = span.get_text(strip=True)
                try:
                    status["ef"] = float(txt.replace(",", "."))
                except:
                    pass
                # Ou do tooltip
                if "ef" not in status:
                    tt = span.get("data-tooltip", "")
                    m = re.search(r"~([\d,]+)", tt)
                    if m:
                        status["ef"] = float(m.group(1).replace(",", "."))
        # Fallback: busca no texto
        if "ef" not in status:
            for tag in soup.find_all(["td", "div", "span"]):
                txt = tag.get_text(" ", strip=True)
                m = re.search(r"Eficiência em batalha[:\s]+([\d,]+)", txt)
                if m:
                    status["ef"] = float(m.group(1).replace(",", "."))
                    break

        # Level
        lv = soup.find("a", class_="tooltip", attrs={"data-tooltip": re.compile(r"Level:\s*\d+")})
        if lv:
            m = re.search(r"Level:\s*(\d+)", lv["data-tooltip"])
            if m: status["level"] = int(m.group(1))

        # Atributos via tooltips
        attr_map = {
            "Strength": "forca",
            "Stamina": "resistencia",
            "Dexterity": "agilidade",
            "Fighting ability": "arte_combate",
            "Parry": "bloqueio",
            "Armour skill": "sk_armadura",
            "One-handed attack": "sk_1mao",
            "Two-handed attack": "sk_2maos",
        }
        for tag in soup.find_all("a", class_="tooltip"):
            tt = tag.get("data-tooltip", "")
            for eng, key in attr_map.items():
                if tt.startswith(eng + ":"):
                    # "Stamina: 52 - 5" ou "Strength: 52 + 2" → calcula final
                    m = re.search(r":\s*(\d+)\s*([+-])\s*(\d+)", tt)
                    if m:
                        base  = int(m.group(1))
                        sinal = 1 if m.group(2) == "+" else -1
                        mod   = int(m.group(3))
                        status[key] = base + sinal * mod
                    else:
                        m = re.search(r"(\d+)", tt)
                        if m: status[key] = int(m.group(1))

        # HP
        hp_tag = soup.find("div", class_="charlife")
        if hp_tag:
            m = re.search(r"([\d.]+)", hp_tag.get_text().replace(".", "").replace(",", ""))
            if m: status["hp_total"] = int(m.group(1))

    except Exception as e:
        log.warning(f"parsear_status_bg: {e}")
    return status

def parsear_sessao_bg(soup):
    """Extrai info da sessão atual do BG (multi-idioma: PT/EN/DE/PL)."""
    sessao = {}
    try:
        texto = soup.get_text(" ", strip=True)

        # Padrões multilíngua: "X de um máximo de Y" (PT) / "X of a maximum of Y" (EN)
        # / "X von maximal Y" (DE) — captura qualquer par (número, número-limite)
        _PATTERNS_MAX = [
            r"(\d+)\s+de\s+um\s+m[aá]ximo\s+de\s+(\d+)",          # PT
            r"(\d+)\s+of\s+a\s+maximum\s+of\s+(\d+)",              # EN
            r"(\d+)\s+of\s+(?:the\s+)?maximum\s+(\d+)",            # EN alt
            r"(\d+)\s+von\s+maximal\s+(\d+)",                      # DE
            r"(\d+)\s+z\s+maksymalnie\s+(\d+)",                    # PL
            r"(\d+)\s*/\s*(\d+)\s*(?:battle|Kampf|walka|boj)",     # genérico "X/Y battle"
        ]
        matches = []
        for pat in _PATTERNS_MAX:
            matches.extend(re.findall(pat, texto, re.IGNORECASE))

        for val_s, max_s in matches:
            val, maximo = int(val_s), int(max_s)
            if maximo == 100:
                sessao["batalhas_dia"]     = val
                sessao["batalhas_dia_max"] = maximo
            elif maximo in (200, 400):
                sessao["batalhas_total"]  = maximo
                sessao["batalhas_feitas"] = val

        # Se modo free (max=100), total = dia
        if "batalhas_total" not in sessao and "batalhas_dia" in sessao:
            sessao["batalhas_total"]  = sessao["batalhas_dia_max"]
            sessao["batalhas_feitas"] = sessao["batalhas_dia"]

        # Restante do dia
        if "batalhas_dia" in sessao:
            sessao["restantes_hoje"] = sessao["batalhas_dia_max"] - sessao["batalhas_dia"]

        # Datas — multilíngua
        _DATA_INICIO = [
            r"In[ií]cio da sess[aã]o[^:]*:\s*([\d.]+\s[\d:]+)",   # PT
            r"Session\s+start[^:]*:\s*([\d.]+\s[\d:]+)",           # EN
            r"Sessionsbeginn[^:]*:\s*([\d.]+\s[\d:]+)",            # DE
            r"Pocz[aą]tek\s+sesji[^:]*:\s*([\d.]+\s[\d:]+)",       # PL
        ]
        for pat in _DATA_INICIO:
            m = re.search(pat, texto, re.IGNORECASE)
            if m: sessao["inicio"] = m.group(1); break

        _DATA_FIM = [
            r"Fim da sess[aã]o[^:]*:\s*([\d.]+\s[\d:]+)",          # PT
            r"Session\s+end[^:]*:\s*([\d.]+\s[\d:]+)",             # EN
            r"Sessionsende[^:]*:\s*([\d.]+\s[\d:]+)",              # DE
            r"Koniec\s+sesji[^:]*:\s*([\d.]+\s[\d:]+)",            # PL
        ]
        for pat in _DATA_FIM:
            m = re.search(pat, texto, re.IGNORECASE)
            if m: sessao["fim"] = m.group(1); break

        # Restantes multilíngua
        _RESTANTES = [
            r"pode realizar mais\s+(\d+)\s+ataques",               # PT
            r"can\s+(?:still\s+)?perform\s+(?:up\s+to\s+)?(\d+)\s+attack",  # EN
            r"noch\s+(\d+)\s+Kampf",                               # DE
            r"mo[żz]esz\s+jeszcze\s+przeprowadzi[ćc]\s+(\d+)",    # PL
        ]
        for pat in _RESTANTES:
            m = re.search(pat, texto, re.IGNORECASE)
            if m: sessao["restantes_hoje"] = int(m.group(1)); break

        # Fallback de contagem: se padrões numéricos não bateram mas a página tem
        # sinais de BG ativo — via HTML ou texto — marca sessão ativa minimamente
        if "batalhas_total" not in sessao and "batalhas_dia" not in sessao:
            _sinais_html = (
                soup.find("form", action=lambda a: a and "battleground" in (a or "").lower())
                or soup.find("a", href=lambda h: h and ("/battleground/attack" in (h or "") or "wac=fight" in (h or "")))
                or soup.find("a", href=lambda h: h and "/battleground/currentbattle" in (h or ""))
            )
            _sinais_texto = any(p in texto.lower() for p in [
                "início da sessão", "session start", "sessionsbeginn", "pocz",
                "fim da sessão", "session end", "sessionsende",
                "batalhas hoje", "battles today", "kämpfe heute",
                "restantes", "remaining", "verbleibende",
            ])
            if _sinais_html or _sinais_texto:
                log.debug("parsear_sessao_bg: padrões de contagem não encontrados mas BG ativo detectado — marcando sessão")
                sessao["batalhas_total"]  = 100
                sessao["batalhas_feitas"] = 0

        # Fallback de início: se a página carregou mas data não foi parseada,
        # usa chave sintética — garante que o bot não abortará por "sem sessão"
        if "inicio" not in sessao:
            log.debug(f"parsear_sessao_bg: data de início não encontrada — texto: {texto[:300]}")
            sessao["inicio"] = f"sess_{datetime.now():%Y%m%d}"

    except Exception as e:
        log.warning(f"parsear_sessao_bg: {e}")
    return sessao

def parsear_estatisticas_bg(soup):
    """Extrai estatísticas da sessão BG (multilíngua: PT/EN/DE/PL)."""
    stats = {}
    try:
        tds = soup.find_all("td", class_="tdn")
        i = 0
        while i < len(tds) - 1:
            label = tds[i].get_text(strip=True).lower()
            valor = tds[i+1].get_text(strip=True).replace(".", "").replace(",", "")
            m = re.search(r"\d+", valor)
            if m:
                v = int(m.group())
                # Batalhas ofensivas / Offensive battles / Offensive Kämpfe / Bitwy ofensywne
                if any(x in label for x in ("ofensiva", "offensive", "offensiv", "ofensywn", "attack")):
                    stats["batalhas"] = v
                # Vencidos / Won / Gewonnen / Wygrane
                elif any(x in label for x in ("vencido", "won", "gewonnen", "wygran", "victori", "win")):
                    stats["vitorias"] = v
                # Derrotas / Lost / Verloren / Przegrane
                elif any(x in label for x in ("derrota", "lost", "verloren", "przegran", "defeat")):
                    stats["derrotas"] = v
                # Empates / Draws / Unentschieden / Remisy
                elif any(x in label for x in ("empate", "draw", "unentschied", "remis")):
                    stats["empates"] = v
                # Pontos de batalha / Battle points / Kampfpunkte / Punkty bitewne
                elif any(x in label for x in ("batalha ganho", "battle point", "kampfpunkt", "punkty bitew", "points earned")):
                    stats["pontos_batalha"] = v
                # Ouro ganho / Gold earned / Gold verdient / Złoto zdobyte
                elif any(x in label for x in ("ouro ganho", "gold earned", "gold verdient", "złoto", "gold gain")):
                    stats["gold"] = v
                # Dano causado / Hit points dealt / Trefferpunkte verursacht
                elif any(x in label for x in ("atribuíd", "dealt", "verursacht", "zadanych", "caused")):
                    stats["dano_causado"] = v
                # Dano recebido / Hit points received / Trefferpunkte erhalten
                elif any(x in label for x in ("sofrido", "received", "erhalten", "otrzyman", "taken")):
                    stats["dano_recebido"] = v
            i += 2
    except Exception as e:
        log.warning(f"parsear_estatisticas_bg: {e}")
    return stats

def parsear_adversarios(soup):
    """
    Extrai lista de adversários da busca BG.
    O BG usa cards com classe 'fsbox' mas estrutura interna diferente.
    Atributos aparecem como texto direto nos divs com barras visuais.
    """
    adversarios = []
    try:
        cards = soup.find_all("div", class_="fsbox")
        log.debug(f"parsear_adversarios: {len(cards)} cards encontrados")

        for fsbox in cards:
            adv = {}

            # Nome
            nome_tag = fsbox.find("div", class_="enemyname")
            if nome_tag:
                adv["nome"] = nome_tag.get_text(strip=True)

            # ID e csrf do form de ataque
            gegnerid = fsbox.find("input", {"name": "gegnerid"})
            if gegnerid: adv["id"] = gegnerid["value"]
            csrf = fsbox.find("input", {"name": "csrftoken"})
            if csrf: adv["csrf"] = csrf["value"]

            # Tipo: zumbi ou humano
            nat = fsbox.find("div", class_="fsnattxt")
            if nat:
                txt = nat.get_text(strip=True).lower()
                adv["tipo"] = "zumbi" if ("morto" in txt or "npc" in txt or "undead" in txt) else "humano"
            else:
                adv["tipo"] = "desconhecido"

            # Level e EF da tabela fsbint2
            tbl2 = fsbox.find("table", class_="fsbint2")
            if tbl2:
                rows = tbl2.find_all("tr")
                for row in rows:
                    tds = row.find_all("td")
                    if len(tds) < 2: continue
                    label = tds[0].get_text(strip=True).lower()
                    val   = tds[1].get_text(strip=True).replace("~","").strip()
                    try:
                        v = float(val.replace(",","."))
                        if "level" in label: adv["level"] = int(v)
                        elif "efici" in label: adv["ef"] = v
                        elif "vital" in label: adv["hp"] = int(v)
                    except: pass

            # Skills da tabela fsbint3
            tbl3 = fsbox.find("table", class_="fsbint3")
            if tbl3:
                for row in tbl3.find_all("tr"):
                    tds = row.find_all("td")
                    if len(tds) < 2: continue
                    # Label está no td com fsbtitle div
                    title = row.find("div", class_="fsbtitle")
                    valtd = tds[-1]
                    if not title: continue
                    label = title.get_text(strip=True).lower()
                    try:
                        v = int(valtd.get_text(strip=True))
                        if "equip" in label: adv["sk_armadura"] = v
                        elif "uma" in label: adv["sk_1mao"] = v
                        elif "duas" in label: adv["sk_2maos"] = v
                    except: pass

            # Atributos da tabela fsbarbox
            tbl4 = fsbox.find("table", class_="fsbarbox")
            if tbl4:
                for row in tbl4.find_all("tr"):
                    tds = row.find_all("td")
                    if len(tds) < 2: continue
                    title = row.find("div", class_="fsbtitle")
                    valtd = tds[-1]
                    if not title: continue
                    label = title.get_text(strip=True).lower()
                    val_el = valtd.find("div", class_="sk4") or valtd
                    try:
                        v = int(val_el.get_text(strip=True))
                        if "força" in label or "strength" in label: adv["forca"] = v
                        elif "resist" in label or "stamina" in label: adv["resistencia"] = v
                        elif "agilidade" in label or "dexterity" in label: adv["agilidade"] = v
                        elif "arte" in label or "fighting" in label: adv["arte_combate"] = v
                        elif "bloqueio" in label or "parry" in label: adv["bloqueio"] = v
                    except: pass

            if adv.get("id") and adv.get("nome"):
                adversarios.append(adv)
            elif adv.get("nome"):
                log.debug(f"  Card sem ID: {adv.get('nome')} — ignorado")

    except Exception as e:
        log.warning(f"parsear_adversarios: {e}", exc_info=True)

    return adversarios

def parsear_turnos_combate_bg(turns_json):
    """Extrai estatísticas dos turnos do BG (atacante = eu no BG)."""
    stats = {
        "hits_eu": 0, "misses_eu": 0,
        "hits_adv": 0, "misses_adv": 0,
        "taxa_acerto_eu": 0, "taxa_acerto_adv": 0,
        "rounds": 0, "crits_eu": 0, "crits_adv": 0,
        "dano_bloqueado_eu": 0.0,
    }
    if not turns_json: return stats
    for t in turns_json:
        p, acao = t.get("p",""), t.get("a","")
        dano = float(t.get("d",0) or 0)
        bloq = float(t.get("b",0) or 0)
        crit = bool(t.get("c", False))
        if p == "a":  # atacante = eu
            stats["rounds"] += 1
            if acao == "h":
                stats["hits_eu"] += 1
                stats["dano_bloqueado_adv"] = stats.get("dano_bloqueado_adv",0) + bloq
                if crit: stats["crits_eu"] += 1
            else:
                stats["misses_eu"] += 1
        elif p == "d":  # defensor = adv
            if acao == "h":
                stats["hits_adv"] += 1
                stats["dano_bloqueado_eu"] += bloq
                if crit: stats["crits_adv"] += 1
            else:
                stats["misses_adv"] += 1
    te = stats["hits_eu"] + stats["misses_eu"]
    ta = stats["hits_adv"] + stats["misses_adv"]
    stats["taxa_acerto_eu"]  = round(stats["hits_eu"]  / te * 100, 1) if te > 0 else 0
    stats["taxa_acerto_adv"] = round(stats["hits_adv"] / ta * 100, 1) if ta > 0 else 0
    stats["dano_bloqueado_eu"] = round(stats["dano_bloqueado_eu"], 1)
    return stats


def parsear_resultado_combate(soup):
    """Extrai resultado do combate BG incluindo stats de turnos."""
    resultado = "desconhecido"
    gold = 0
    xp = 0
    dano_causado = 0
    dano_recebido = 0
    turnos_stats = {}
    try:
        for script in soup.find_all("script"):
            txt = script.string or ""
            if "displayFightReport" not in txt: continue
            m = re.search(r'"winner"\s*:\s*"(\w+)"', txt)
            if m:
                winner = m.group(1)
                resultado = "vitoria" if winner == "attacker" else "derrota"
            try:
                import json as _json
                m_turns = re.search(r'"turns"\s*:\s*(\[.*?\])', txt, re.DOTALL)
                if m_turns:
                    turns = _json.loads(m_turns.group(1))
                    turnos_stats = parsear_turnos_combate_bg(turns)
            except Exception: pass
            break

        html_txt = str(soup)
        m = re.search(r"(\d+)\s*<img[^>]*gold[^>]*>", html_txt)
        if m: gold = int(m.group(1))

        m_xp = re.findall(r"(\d+)\s*<img[^>]*exp_scroll[^>]*>", html_txt)
        for v in m_xp:
            if int(v) > 0: xp = int(v); break

        danos_atk = re.findall(r'class="attacker"[^>]*>.*?(\d+[\.,]\d+)\s*points of damage', html_txt)
        danos_def = re.findall(r'class="defender"[^>]*>.*?(\d+[\.,]\d+)\s*points of damage', html_txt)
        dano_causado  = sum(float(d.replace(",",".")) for d in danos_atk)
        dano_recebido = sum(float(d.replace(",",".")) for d in danos_def)

    except Exception as e:
        log.warning(f"parsear_resultado_combate: {e}")
    return resultado, gold, xp, round(dano_causado,1), round(dano_recebido,1), turnos_stats

# ═══════════════════════════════════════════════════════════════
# SISTEMA DE APRENDIZADO
# ═══════════════════════════════════════════════════════════════
def registrar_combate(eu, adversario, resultado, gold, xp, dano_causado, dano_recebido, turnos=None):
    """Salva combate para aprendizado futuro."""
    combates = carregar_combates()
    registro = {
        "timestamp": agora().isoformat(),
        "resultado": resultado,
        "gold": gold,
        "xp": xp,
        "dano_causado": dano_causado,
        "dano_recebido": dano_recebido,
        # Meus stats
        "eu_ef": eu.get("ef", 0),
        "eu_ac": eu.get("arte_combate", 0),
        "eu_blq": eu.get("bloqueio", 0),
        "eu_frc": eu.get("forca", 0),
        "eu_res": eu.get("resistencia", 0),
        "eu_lv": eu.get("level", 0),
        # Stats do adversário
        "adv_nome": adversario.get("nome", ""),
        "adv_id": adversario.get("id", ""),
        "adv_tipo": adversario.get("tipo", ""),
        "adv_ef": adversario.get("ef", 0),
        "adv_ac": adversario.get("arte_combate", 0),
        "adv_blq": adversario.get("bloqueio", 0),
        "adv_frc": adversario.get("forca", 0),
        "adv_res": adversario.get("resistencia", 0),
        "adv_lv": adversario.get("level", 0),
        "adv_arm": adversario.get("sk_armadura", 0),
        "adv_s1": adversario.get("sk_1mao", 0),
        "adv_s2": adversario.get("sk_2maos", 0),
        # Score previsto e simulador
        "score_previsto": adversario.get("_score", 0),
        "score_sim":      adversario.get("_score_sim", adversario.get("_score", 0)),
        # Dados reais dos turnos para calibrar simulador
        "hits_eu":         (turnos or {}).get("hits_eu", 0),
        "misses_eu":       (turnos or {}).get("misses_eu", 0),
        "hits_adv":        (turnos or {}).get("hits_adv", 0),
        "misses_adv":      (turnos or {}).get("misses_adv", 0),
        "taxa_acerto_eu":  (turnos or {}).get("taxa_acerto_eu", 0),
        "taxa_acerto_adv": (turnos or {}).get("taxa_acerto_adv", 0),
        "rounds_real":     (turnos or {}).get("rounds", 0),
        "crits_eu":        (turnos or {}).get("crits_eu", 0),
        "dano_bloqueado_eu": (turnos or {}).get("dano_bloqueado_eu", 0),
    }
    combates.append(registro)
    salvar_combates(combates)
    return registro

def calcular_insights(combates):
    """Calcula correlações aprendidas dos combates."""
    if len(combates) < 5:
        return {"mensagem": f"Aguardando mais combates ({len(combates)}/5 mínimo)"}

    total = len(combates)
    vitorias = sum(1 for c in combates if c["resultado"] == "vitoria")
    wr_global = vitorias / total * 100

    # WR por faixa de EF do adversário
    faixas = {}
    for c in combates:
        ef = c.get("adv_ef", 0)
        faixa = f"{int(ef*2)/2:.1f}"  # arredonda para 0.5
        if faixa not in faixas:
            faixas[faixa] = {"total": 0, "vit": 0}
        faixas[faixa]["total"] += 1
        if c["resultado"] == "vitoria":
            faixas[faixa]["vit"] += 1

    faixas_wr = {k: round(v["vit"]/v["total"]*100, 1) for k, v in faixas.items() if v["total"] >= 2}

    # Correlação AC vs resultado
    hits_altos_wr = sum(1 for c in combates
                        if c.get("adv_ac", 0) > 0 and
                        c["adv_ac"] / (c["adv_ac"] + c["eu_blq"] + 1) > 0.55
                        and c["resultado"] == "vitoria")
    hits_baixos_total = sum(1 for c in combates
                            if c.get("adv_ac", 0) > 0 and
                            c["adv_ac"] / (c["adv_ac"] + c["eu_blq"] + 1) <= 0.55)

    # Média de dano causado em vitórias vs derrotas
    dano_vit = [c["dano_causado"] for c in combates if c["resultado"] == "vitoria"]
    dano_der = [c["dano_causado"] for c in combates if c["resultado"] == "derrota"]

    return {
        "total_combates": total,
        "win_rate": round(wr_global, 1),
        "wr_por_ef": faixas_wr,
        "media_dano_vitoria": round(sum(dano_vit)/len(dano_vit), 1) if dano_vit else 0,
        "media_dano_derrota": round(sum(dano_der)/len(dano_der), 1) if dano_der else 0,
        "gold_total": sum(c["gold"] for c in combates),
        "xp_total": sum(c["xp"] for c in combates),
    }

# ═══════════════════════════════════════════════════════════════
# AVALIAÇÃO DE ADVERSÁRIOS
# ═══════════════════════════════════════════════════════════════
def avaliar_adversario_bg(adv, eu, combates=None):
    """
    Score 0-100 para chance de vitória no BG.
    Combina fórmula base com aprendizado de combates anteriores.
    """
    minha_ac  = eu.get("arte_combate", 0)
    meu_blq   = eu.get("bloqueio", 0)
    minha_frc = eu.get("forca", 0)
    meu_lv    = eu.get("level", 0)

    blq    = adv.get("bloqueio", 0)
    ac_d   = adv.get("arte_combate", 0)
    frc_d  = adv.get("forca", 0)
    lv_d   = adv.get("level", 0)
    arm_d  = adv.get("sk_armadura", 0)
    agil_d = adv.get("agilidade", 0)
    sk1_d  = adv.get("sk_1mao", 0)
    sk2_d  = adv.get("sk_2maos", 0)
    sk_d   = max(sk1_d, sk2_d)
    ef_d   = adv.get("ef", 0)
    meu_sk1  = eu.get("sk_1mao", 0)
    meu_sk2  = eu.get("sk_2maos", 0)
    meu_sk   = max(meu_sk1, meu_sk2)
    # Detecta build
    usa_2h  = sk2_d > sk1_d and sk2_d > 0
    usa_arm = arm_d > 20

    score = 50
    problemas = []
    vantagens = []

    # ── 1. Level delta ──────────────────────────────────────────
    delta_lv = lv_d - meu_lv
    if delta_lv >= 10:
        problemas.append(f"Level +{delta_lv} — equipamento muito superior")
        score -= 30
    elif delta_lv >= 7:
        problemas.append(f"Level +{delta_lv} — equipamento superior")
        score -= 18
    elif delta_lv >= 4:
        score -= 8
    elif delta_lv <= -4:
        vantagens.append(f"Level {delta_lv} inferior ✓")
        score += 8

    # ── 2. Hit rate (meu AC vs bloqueio dele) ──────────────────
    taxa = minha_ac / (minha_ac + blq) if blq > 0 else 1.0
    if blq > 0:
        if taxa < 0.35:
            problemas.append(f"Hit rate {taxa*100:.0f}% — bloqueio {blq} absurdo")
            score -= 35
        elif taxa < 0.45:
            problemas.append(f"Hit rate {taxa*100:.0f}% — difícil acertar")
            score -= 20
        elif taxa < 0.52:
            score -= 8
        else:
            vantagens.append(f"Hit rate {taxa*100:.0f}% ✓")
            score += 15

    # ── 3. Hit rate dele (AC dele vs meu bloqueio) ─────────────
    taxa_d = ac_d / (ac_d + meu_blq) if ac_d > 0 and meu_blq > 0 else 0.0
    if ac_d > 0 and meu_blq > 0:
        if taxa_d > 0.70:
            problemas.append(f"AC dele {ac_d} acerta {taxa_d*100:.0f}%")
            score -= 20
        elif taxa_d > 0.58:
            score -= 8
        elif taxa_d < 0.45:
            vantagens.append(f"Meu bloqueio segura {(1-taxa_d)*100:.0f}% ✓")
            score += 12

    # ── 4. Build especializada (AC e Blq ambos superiores) ─────
    if ac_d > minha_ac and blq > meu_blq:
        vantagem_dupla = ((ac_d - minha_ac) + (blq - meu_blq)) / 2
        if vantagem_dupla > 15:
            problemas.append(f"Build especializada: AC+Blq ambos superiores")
            score -= 20
        elif vantagem_dupla > 8:
            score -= 12

    # ── 5. Skill de ataque (1h ou 2h) ────────────────────────────
    if sk_d > 0 and meu_sk > 0:
        diff_sk = sk_d - meu_sk
        if diff_sk > 30:
            problemas.append(f"Skill ataque {sk_d} vs minha {meu_sk}")
            score -= 15
        elif diff_sk > 15:
            score -= 8
        elif diff_sk < -20:
            vantagens.append(f"Skill ataque superior {meu_sk} vs {sk_d} ✓")
            score += 10

    # ── 6. Força (dano bruto) ──────────────────────────────────
    if frc_d > minha_frc * 2.0:
        score -= 20
    elif frc_d > minha_frc * 1.5:
        score -= 10
    elif frc_d > 0 and frc_d < minha_frc * 0.7:
        vantagens.append(f"Força {frc_d} baixa ✓")
        score += 8

    # ── 6. Armadura + Agilidade (defesa real) ─────────────────
    if usa_arm:
        defesa_efetiva = arm_d + max(0, agil_d // 5)
        if defesa_efetiva > 60:
            problemas.append(f"Defesa alta: arm={arm_d} agil={agil_d}")
            score -= 18
        elif defesa_efetiva > 35:
            score -= 10
        elif agil_d < -5:
            vantagens.append(f"Armadura com agil negativa {agil_d} — defesa reduzida ✓")
            score += 5
    else:
        if usa_2h:
            vantagens.append(f"Build 2h sem armadura — defesa mínima ✓")
            score += 8

    # ── 6b. Build 2h: penalidade pelo dano alto ────────────────
    if usa_2h and sk2_d > meu_sk * 1.3:
        problemas.append(f"Build 2h skill {sk2_d} — dano alto")
        score -= 12
    elif usa_2h and sk2_d > meu_sk:
        score -= 5

    # ── 7. Detecção de build ruim (zumbi mal configurado) ──────
    # Build ruim: AC muito baixo para o level, ou skills dispersas
    ac_esperado = 40 + lv_d * 1.5  # estimativa para level
    build_ruim = False
    if ac_d > 0 and ac_d < ac_esperado * 0.6:
        vantagens.append(f"Build fraca: AC {ac_d} baixo para Lv{lv_d} ✓")
        score += 15
        build_ruim = True
    # Zumbi com skills dispersas (tem pontos em 1 mão E 2 mãos)
    s1 = adv.get("sk_1mao", 0)
    s2 = adv.get("sk_2maos", 0)
    if s1 > 10 and s2 > 10:
        vantagens.append(f"Skills dispersas: 1h={s1} e 2h={s2} ✓")
        score += 10
        build_ruim = True

    score = max(0, min(100, score))

    # ── Simulação de combate ──────────────────────────────────────────────────
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from combat_sim import simular_combate
        sim = simular_combate(eu, adv)

        sim_score = sim["score"]  # mecânica BG = PvP normal (rounds por resistência)
        score = max(0, min(100, sim_score))
        adv["_score_sim"] = sim_score
    except Exception:
        pass

    # ── 8. Ajuste por aprendizado — aplicado sobre sim corrigido ─────────────
    if combates and len(combates) >= 10:
        ef_adv = ef_d
        similares = [c for c in combates
                     if abs(c.get("adv_ef", 0) - ef_adv) <= 0.5]
        if len(similares) >= 3:
            wr_similar = sum(1 for c in similares if c["resultado"] == "vitoria") / len(similares)
            score_aprendido = wr_similar * 100
            # Peso do aprendizado cresce com número de combates (máx 60%)
            peso = min(0.60, 0.30 + len(similares) * 0.005)
            score = round(score * (1 - peso) + score_aprendido * peso)
            log.debug(f"  Aprendizado: {len(similares)} similares → WR {wr_similar*100:.0f}% "
                      f"(peso {peso:.0%}) → score {score}")

    score = max(0, min(100, score))

    rec = "ATACAR" if score >= SCORE_MIN_ATACAR else "EVITAR"

    return {
        "score": score,
        "recomendacao": rec,
        "vantagens": vantagens,
        "problemas": problemas,
        "build_ruim": build_ruim,
        "ef": ef_d,
    }

def escolher_melhor_alvo(adversarios, eu, combates=None):
    """
    Avalia todos adversários e escolhe o melhor.
    Critério: maior (EF × score) entre os atacáveis.
    """
    avaliados = []
    for adv in adversarios:
        av = avaliar_adversario_bg(adv, eu, combates)
        adv["_score"] = av["score"]
        adv["_rec"]   = av["recomendacao"]
        adv["_av"]    = av
        # Pontuação combinada: EF alta + chance de vitória
        ef = adv.get("ef", 0)
        adv["_pontuacao"] = ef * (av["score"] / 100)
        avaliados.append(adv)
        log.info(f"    {adv.get('nome','?')} Lv{adv.get('level','?')} EF{ef} "
                 f"[{adv.get('tipo','?')}] → Score:{av['score']} → {av['recomendacao']} "
                 f"(pontuação: {adv['_pontuacao']:.2f})")

    # Filtra só os atacáveis
    atacaveis = [a for a in avaliados if a["_rec"] == "ATACAR"]
    if not atacaveis:
        return None, avaliados

    # Ordena por pontuação (EF × score) decrescente
    atacaveis.sort(key=lambda x: x["_pontuacao"], reverse=True)
    return atacaveis[0], avaliados

# ═══════════════════════════════════════════════════════════════
# ENTRADA NO BG
# ═══════════════════════════════════════════════════════════════
_MODO_PARA_BOTAO = {"free": "bp1", "medio": "bp2", "premium": "bp3"}


def _dormir_fatias(segundos):
    """
    Dorme em fatias de 30min, verificando sinal de parada a cada fatia.
    Retorna True se completou, False se recebeu parar_bot.
    """
    restante = max(0, int(segundos))
    while restante > 0:
        est = carregar_estado()
        if est.get("parar_bot"):
            return False
        fatia = min(1800, restante)
        time.sleep(fatia)
        restante -= fatia
    return True

def entrar_bg(client, modo, alignment="light"):
    """
    Entra no BG submetendo o form da tela de entrada.
    Retorna:
      "ok"             — entrou com sucesso
      "sem_equipamento"— personagem sem equipamento adequado (aguardar bot principal)
      "falha"          — erro ou requisito não atendido
    """
    botao = _MODO_PARA_BOTAO.get(modo, "bp1")
    log.info(f"Tentando entrar no BG — modo={modo} ({botao}), alignment={alignment}")

    # 1. GET na tela de entrada
    try:
        soup = client.get_main("/battleground/enter/")
    except Exception as e:
        log.error(f"entrar_bg: erro ao acessar página de entrada: {e}")
        return ("falha", 0)

    texto = soup.get_text(" ", strip=True)

    # ── Detecta sessão BG já ativa ────────────────────────────────────────────
    # Quando BG está aberto, /battleground/enter/ pode redirecionar para a tela
    # de batalha atual (sem form de entrada), ou mostrar link para currentbattle.
    _currentbattle_link = soup.find("a", href=lambda h: h and "/battleground/currentbattle" in (h or ""))
    _sem_form_entrada    = not soup.find("input", {"name": "csrftoken"}) and not soup.find("button", class_="startbs")
    _texto_em_sessao = any(p in texto.lower() for p in [
        "current battle", "batalha atual", "aktuelle", "currentbattle",
        "session active", "sessão ativa", "active session",
        "em sessão", "ya en sesión",
    ])
    if _currentbattle_link or (_sem_form_entrada and _texto_em_sessao):
        log.info("entrar_bg: BG já está em sessão ativa — pulando entrada")
        return ("em_sessao", 0)

    # Sem form de entrada e sem nok.gif de requisitos → sessão ativa (falso negativo do check acima)
    # /landsitz/ aparece no menu de navegação em todas as páginas, não é sinal exclusivo de equipamento
    if _sem_form_entrada:
        _tds_nok = soup.find_all("td", class_="bsseltd")
        _tem_nok_geral = any(
            img for td in _tds_nok for img in td.find_all("img")
            if "nok.gif" in img.get("src", "")
        )
        if not _tem_nok_geral:
            log.info("entrar_bg: sem form de entrada e sem requisitos pendentes — sessão ativa inferida")
            return ("em_sessao", 0)

    # Detecta tela de "equipe seu cavaleiro" — personagem sem itens adequados
    # Só avalia quando há form de entrada (palavras como "equipe"/"arma" são genéricas em PT)
    if not _sem_form_entrada:
        tem_aviso_equip = any(p in texto for p in [
            "Equip your knight", "optimally",
            "ausrüsten", "équiper",  # DE/FR específicos
        ])
        if tem_aviso_equip:
            log.warning("entrar_bg: personagem sem equipamento adequado — tela de aviso detectada")
            return ("sem_equipamento", 0)

    # ── Verifica requisitos ANTES de checar botão ──────────────────────────
    # (quando o modo está em cooldown, o jogo não renderiza o botão — mas ainda
    #  mostra notok.gif na coluna de requisitos; checar primeiro evita retornar "falha")
    tds = soup.find_all("td", class_="bsseltd")
    idx_botao = {"bp1": 0, "bp2": 1, "bp3": 2}.get(botao, 0)
    dias_requeridos = {"bp1": 2, "bp2": 1, "bp3": 0}.get(botao, 0)
    if idx_botao < len(tds):
        nok_imgs = [img for img in tds[idx_botao].find_all("img") if "nok.gif" in img.get("src", "")]
        if nok_imgs:
            nivel_faltando = False
            sessao_faltando = False
            for img in nok_imgs:
                span = img.find_next_sibling("span")
                t = span.get_text(strip=True).lower() if span else ""
                if any(p in t for p in ["nível", "level", "nivel"]):
                    nivel_faltando = True
                if any(p in t for p in ["sessão", "session", "sessao", "batalha"]):
                    sessao_faltando = True

            if nivel_faltando and not sessao_faltando:
                log.warning("entrar_bg: nível insuficiente — BG requer Lv10")
                return ("nivel_insuficiente", 3600)

            # Cooldown de sessão — extrai data exata da página
            wait_seg = dias_requeridos * 86400  # fallback: N dias completos
            for _pat in [
                r"terminou[^(]*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                r"ended[^(]*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                r"\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
            ]:
                m_data = re.search(_pat, texto, re.IGNORECASE)
                if m_data:
                    break
            if m_data:
                try:
                    dt_fim = datetime.strptime(m_data.group(1), "%d.%m.%Y %H:%M:%S")
                    dt_pode = dt_fim + timedelta(days=dias_requeridos)
                    wait_seg = max(60, int((dt_pode - datetime.now()).total_seconds()) + 60)
                    log.warning(
                        f"entrar_bg: cooldown — última sessão {dt_fim:%d/%m %H:%M} | "
                        f"pode entrar em {dt_pode:%d/%m %H:%M} ({fmt_t(wait_seg)} restantes)"
                    )
                except Exception:
                    log.warning(f"entrar_bg: cooldown — aguardando {fmt_t(wait_seg)} (fallback)")
            else:
                log.warning(f"entrar_bg: requisito não atendido — aguardando {fmt_t(wait_seg)} (fallback)")
            return ("cooldown", wait_seg)

    # Extrai csrf do form
    csrf_input = soup.find("input", {"name": "csrftoken"})
    if not csrf_input:
        # Sem form — verifica se há nok.gif (cooldown) antes de retornar falha
        _nok_sem_csrf = soup.find_all("img", src=lambda s: s and "nok.gif" in s)
        if _nok_sem_csrf:
            wait_seg = dias_requeridos * 86400
            for _pat in [
                r"terminou[^(]*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                r"ended[^(]*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                r"\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
            ]:
                _m = re.search(_pat, texto, re.IGNORECASE)
                if _m:
                    try:
                        dt_fim = datetime.strptime(_m.group(1), "%d.%m.%Y %H:%M:%S")
                        dt_pode = dt_fim + timedelta(days=dias_requeridos)
                        wait_seg = max(60, int((dt_pode - datetime.now()).total_seconds()) + 60)
                        log.warning(
                            f"entrar_bg: cooldown (sem form) — última sessão {dt_fim:%d/%m %H:%M} | "
                            f"pode entrar em {dt_pode:%d/%m %H:%M} ({fmt_t(wait_seg)} restantes)"
                        )
                    except Exception:
                        log.warning(f"entrar_bg: cooldown (sem form, nok.gif) — aguardando {fmt_t(wait_seg)} (fallback)")
                    return ("cooldown", wait_seg)
            log.warning(f"entrar_bg: cooldown (sem form, nok.gif, sem data) — aguardando {fmt_t(wait_seg)} (fallback)")
            return ("cooldown", wait_seg)
        log.error("entrar_bg: csrftoken não encontrado na página de entrada")
        return ("falha", 0)
    csrf = csrf_input.get("value", "")

    # Verifica se o botão do modo escolhido existe
    botao_tag = soup.find("button", {"name": botao})
    if not botao_tag:
        disponiveis = [b["name"] for b in soup.find_all("button", class_="startbs") if b.get("name")]
        if disponiveis:
            # Botão ausente mas outros modos disponíveis → cooldown de sessão para este modo
            log.warning(f"entrar_bg: botão {botao} não encontrado — modo {modo} em cooldown")
            log.info(f"  Botões disponíveis: {disponiveis}")
            wait_seg = dias_requeridos * 86400  # fallback N dias
            for _pat in [
                r"terminou[^(]*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                r"ended[^(]*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                r"\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
            ]:
                _m = re.search(_pat, texto, re.IGNORECASE)
                if _m:
                    try:
                        dt_fim = datetime.strptime(_m.group(1), "%d.%m.%Y %H:%M:%S")
                        dt_pode = dt_fim + timedelta(days=dias_requeridos)
                        wait_seg = max(60, int((dt_pode - datetime.now()).total_seconds()) + 60)
                        log.warning(
                            f"  Última sessão {dt_fim:%d/%m %H:%M} | "
                            f"pode entrar em {dt_pode:%d/%m %H:%M} ({fmt_t(wait_seg)} restantes)"
                        )
                    except Exception:
                        pass
                    break
            else:
                log.warning(f"  Data da última sessão não encontrada — aguardando {fmt_t(wait_seg)} (fallback)")
            return ("cooldown", wait_seg)
        else:
            # Nenhum botão disponível — verifica se é cooldown global (todos os modos em CD)
            # antes de retornar falha (página pode não renderizar botões quando em cooldown)
            _nok_global = soup.find_all("img", src=lambda s: s and "nok.gif" in s)
            wait_seg = dias_requeridos * 86400
            if _nok_global:
                for _pat in [
                    r"terminou[^(]*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                    r"ended[^(]*\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                    r"\((\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})\)",
                ]:
                    _m = re.search(_pat, texto, re.IGNORECASE)
                    if _m:
                        try:
                            dt_fim = datetime.strptime(_m.group(1), "%d.%m.%Y %H:%M:%S")
                            dt_pode = dt_fim + timedelta(days=dias_requeridos)
                            wait_seg = max(60, int((dt_pode - datetime.now()).total_seconds()) + 60)
                            log.warning(
                                f"entrar_bg: todos modos em cooldown — última sessão {dt_fim:%d/%m %H:%M} | "
                                f"pode entrar em {dt_pode:%d/%m %H:%M} ({fmt_t(wait_seg)} restantes)"
                            )
                        except Exception:
                            log.warning(f"entrar_bg: todos modos em cooldown — aguardando {fmt_t(wait_seg)} (fallback)")
                        return ("cooldown", wait_seg)
                log.warning(f"entrar_bg: todos modos em cooldown (nok.gif detectado, sem data) — aguardando {fmt_t(wait_seg)} (fallback)")
                return ("cooldown", wait_seg)
            log.error(f"entrar_bg: botão {botao} não encontrado e nenhum modo disponível — verifique equipamento/nível")
            return ("falha", 0)

    # 2. POST para entrar
    try:
        soup_result = client.post_main(
            "/battleground/enter/",
            data={"csrftoken": csrf, "alignment": alignment, botao: ""},
            referer=client.base_url + "/battleground/enter/",
        )
    except Exception as e:
        log.error(f"entrar_bg: erro no POST: {e}")
        return ("falha", 0)

    texto_r = soup_result.get_text(" ", strip=True)

    # Detecta tela de equipamento na resposta (POST) — /landsitz/ no menu não conta
    _resp_sem_form = not soup_result.find("input", {"name": "csrftoken"}) and not soup_result.find("button", class_="startbs")
    if _resp_sem_form:
        _resp_cb = soup_result.find("a", href=lambda h: h and "/battleground/currentbattle" in (h or ""))
        if _resp_cb:
            log.info("entrar_bg: resposta redireciona para sessão ativa")
            return ("em_sessao", 0)
    _resp_aviso_equip = any(p in texto_r for p in ["Equip your knight", "optimally", "ausrüsten", "équiper"])
    if _resp_aviso_equip:
        log.warning("entrar_bg: resposta é tela de aviso de equipamento")
        return ("sem_equipamento", 0)

    for p in ["not eligible", "não elegível", "last battle session", "última sessão de batalha deverá"]:
        if p.lower() in texto_r.lower():
            log.error(f"entrar_bg: falha — '{p}' na resposta")
            return ("falha", 0)

    log.info(f"✓ Entrou no BG com sucesso (modo={modo})")
    return ("ok", 0)


# ═══════════════════════════════════════════════════════════════
# BUSCA E ATAQUE
# ═══════════════════════════════════════════════════════════════
def buscar_adversarios(client, eu, ef_range, ef_offset=0):
    """Busca adversários no BG com range de EF especificado."""
    ef_minha = eu.get("ef", 0)
    if not ef_minha:
        log.warning("  EF do personagem desconhecida — usando 2.0 como padrão")
        ef_minha = 2.0
    ef_from  = max(0.5, ef_minha + ef_offset)   # offset negativo = fallback EF menor
    ef_to    = min(99.0, ef_minha + ef_range + ef_offset)

    # Arredonda para 0.5
    ef_from = round(ef_from * 2) / 2
    ef_to   = round(ef_to * 2) / 2

    log.info(f"  Buscando EF {ef_from} - {ef_to}...")

    # BG usa POST para battleserver/?fragment=1
    # Precisa do csrftoken da página atual
    # csrftoken vem do cookie (confirmado pelo Network tab)
    # Pega csrf do cookie (pode haver múltiplos — pega o último)
    try:
        csrf = client.session.cookies.get("csrf", "")
    except Exception:
        all_csrf = [c.value for c in client.session.cookies if c.name == "csrf"]
        csrf = all_csrf[-1] if all_csrf else ""
    if not csrf:
        soup_form = client.get("/raubzug/")
        csrf_input = soup_form.find("input", {"name": "csrftoken"})
        if csrf_input:
            csrf = csrf_input.get("value", "")
    log.debug(f"  csrf: {'OK len='+str(len(csrf)) if csrf else 'VAZIO'}")

    try:
        soup = client.post({
            "csrftoken": csrf,
            "ac": "raubzug",
            "sac": "gegner",
            "searchtype": "random",
            "fpfrom": str(ef_from),
            "fpto": str(ef_to),
            "slots": "0",
            "showOwnBaseValues": "on",
            "search_npcs": "on",
        })
    except Exception as e:
        log.warning(f"  buscar_adversarios: erro HTTP ({type(e).__name__}) — aguardando 10min antes de tentar novamente")
        time.sleep(600)
        return []
    # Debug: salva HTML recebido para análise
    try:
        html_debug = str(soup)
        fsbox_count = html_debug.count('class="fsbox"')
        log.debug(f"  HTML recebido: {len(html_debug)} bytes | fsbox encontrados: {fsbox_count}")
        if fsbox_count == 0:
            dbg_path = WORKDIR / "debug_response.html"
            dbg_path.write_text(html_debug[:5000], encoding="utf-8")
            # Verifica se é página de CD
            if "Apenas pode efetuar" in html_debug or "Secondscounter" in html_debug:
                # Lê Secondscounter do JavaScript da página
                import re as _re
                m = _re.search(r"var Secondscounter\s*=\s*(\d+)", html_debug)
                if m:
                    segundos_cd = int(m.group(1)) + 3  # +3s de margem
                else:
                    segundos_cd = 600  # 10min default
                log.info(f"  Personagem em CD — aguardando {fmt_t(segundos_cd)}...")
                time.sleep(segundos_cd)
                return []  # Tenta de novo após CD
            elif "enemy-list" in html_debug:
                log.debug("  Página tem #enemy-list mas sem fsbox")
            log.debug(f"  HTML salvo em {dbg_path}")
    except Exception as e:
        log.debug(f"  Debug erro: {e}")
    adversarios = parsear_adversarios(soup)
    log.info(f"  {len(adversarios)} adversários encontrados")
    return adversarios

def atacar(client, adversario):
    """Executa ataque no BG."""
    log.info(f"  Atacando {adversario['nome']} (EF {adversario.get('ef',0)})...")
    # csrf vem do cookie
    # Múltiplos cookies csrf podem existir — usa o do adversário (mais fresco)
    csrf = adversario.get("csrf", "")
    if not csrf:
        try:
            csrf = client.session.cookies.get("csrf", "")
        except Exception:
            # Múltiplos cookies csrf — pega o último
            all_csrf = [c.value for c in client.session.cookies if c.name == "csrf"]
            csrf = all_csrf[-1] if all_csrf else ""
    soup = client.post({
        "csrftoken": csrf,
        "ac": "raubzug",
        "sac": "attack",
        "gegnerid": adversario["id"],
    })
    return soup


def _extrair_ticket_bg(soup):
    """Extrai os dígitos do bilhete de loteria do BG a partir das imagens numbers/bX.png."""
    ticket = ""
    div = soup.find("div", id="tickets")
    if div:
        for img in div.find_all("img"):
            src = img.get("src", "")
            m = re.search(r"/numbers/b(\d)\.png", src)
            if m:
                ticket += m.group(1)
    return ticket or "?"


def fazer_raffle_e_sair(client, soup_raffle=None):
    """
    Executa o raffle (tômbola) e encerra a sessão BG.
    soup_raffle: soup já fetched com o form de raffle (evita request extra).
    Retorna True se encerrou normalmente.
    """
    log.info("🎰 Iniciando raffle e encerramento de sessão BG...")

    # Encontra a página com o form de raffle — tenta múltiplos URLs do battleserver
    # pois a página pode estar em /battleground/currentbattle/ e não no root /
    if soup_raffle is None or not soup_raffle.find("input", {"name": "start_raffle"}):
        soup_raffle = None
        for _path in ["/", "/battleground/currentbattle/", "/battleground/"]:
            try:
                _s = client.get_full(_path)
                if _s.find("input", {"name": "start_raffle"}):
                    soup_raffle = _s
                    log.info(f"  Página de raffle encontrada em /battleserver{_path}")
                    break
            except Exception:
                pass

    if soup_raffle is None:
        log.warning("  Página de raffle não encontrada — sessão pode já ter sido encerrada")
        return True

    # Passo 1: Start Raffle
    raffle_input = soup_raffle.find("input", {"name": "start_raffle"})
    if raffle_input:
        csrf_tag = soup_raffle.find("input", {"name": "csrftoken"})
        csrf = csrf_tag["value"] if csrf_tag else ""
        ticket = _extrair_ticket_bg(soup_raffle)
        log.info(f"  Bilhete de loteria: {ticket}")
        try:
            soup2 = client.post(
                {"csrftoken": csrf, "start_raffle": "true"},
                referer=client.bs_url + "/",
                fragment=False,
            )
            log.info("  ✓ Raffle iniciado")
        except Exception as e:
            log.error(f"  Erro ao iniciar raffle: {e}")
            soup2 = soup_raffle
    else:
        log.info("  Raffle não encontrado na página — talvez já concluído")
        soup2 = soup_raffle

    # Passo 2: End battle session (botão "end") — opcional em alguns servidores
    end_input = soup2.find("input", {"name": "end"})
    if not end_input:
        time.sleep(3)
        try:
            soup2 = client.get_full("/")
            end_input = soup2.find("input", {"name": "end"})
        except Exception:
            pass

    if end_input:
        csrf_tag2 = soup2.find("input", {"name": "csrftoken"})
        csrf2 = csrf_tag2["value"] if csrf_tag2 else ""
        end_val = end_input.get("value", "End battle session")
        try:
            soup3 = client.post(
                {"csrftoken": csrf2, "end": end_val},
                referer=client.bs_url + "/",
                fragment=False,
            )
            log.info("  ✓ Sessão BG encerrada")
            texto3 = soup3.get_text(" ", strip=True)
            for linha in ["Batalhas ofensivas", "Vencidos", "Derrotas", "Ouro ganho", "Pontos de batalha"]:
                for p in [linha, linha.lower()]:
                    if p in texto3:
                        m = re.search(rf"{re.escape(p)}[^:]*:\s*([\d.,]+)", texto3, re.IGNORECASE)
                        if m:
                            log.info(f"  {linha}: {m.group(1)}")
                        break
            return True
        except Exception as e:
            log.error(f"  Erro ao encerrar sessão: {e}")
            return False
    else:
        log.warning("  Botão 'End battle session' não encontrado — sessão encerrada pelo raffle")
        return True


def loop_bg(client, eu, modo):
    """Loop principal do bot BG."""
    # Auto-detecta modo pela sessão real (ignora parâmetro modo)
    sessao_eu = eu.get("sessao_bg", carregar_estado().get("sessao_bg", {}))
    modo_auto = detectar_modo_bg(sessao_eu)
    if modo_auto != modo:
        log.info(f"Modo auto-detectado: {modo_auto} (sessão tem {sessao_eu.get('batalhas_total','?')} batalhas)")
        modo = modo_auto
    config_modo = MODOS_BG[modo]
    cooldown    = config_modo["cooldown"]
    max_batalhas = config_modo["batalhas"]

    log.info(f"🏟 Modo: {config_modo['nome']} | Cooldown: {fmt_t(cooldown)} | Max: {max_batalhas}")

    estado = carregar_estado()

    # Reset batalhas_feitas se sessão BG mudou (nova sessão no servidor)
    # Evita que estado antigo bloqueie o bot ao reiniciar
    sessao_atual_id = eu.get("sessao_inicio", "")
    sessao_salva_id = estado.get("sessao_bg_id", "")
    if sessao_atual_id != sessao_salva_id:
        log.info(f"Nova sessão BG detectada — resetando dados da sessão anterior")
        resetar_dados_sessao_bg()
        estado["batalhas_feitas"] = 0
        estado["vitorias"]    = 0
        estado["derrotas"]    = 0
        estado["gold_total"]  = 0
        estado["xp_total"]    = 0
        estado["sessao_bg_id"] = sessao_atual_id
    else:
        estado.setdefault("batalhas_feitas", 0)
        estado.setdefault("vitorias", 0)
        estado.setdefault("derrotas", 0)
        estado.setdefault("gold_total", 0)
        estado.setdefault("xp_total", 0)

    estado["modo"] = modo
    estado["eu"] = eu
    salvar_estado(estado)

    _falhas_sessao_consec = 0  # contador de vezes que /currentbattle/ retornou vazio

    while True:
        estado = carregar_estado()

        # Verifica flag de parada do dashboard
        if estado.get("parar_bot"):
            log.info("🛑 Bot BG parado pelo dashboard")
            estado.pop("parar_bot", None)
            salvar_estado(estado)
            atualizar_ciclo("status", "parado")
            break

        # Verifica flag de pausa
        while estado.get("pausado"):
            log.info("⏸ Bot BG pausado — aguardando retomada pelo dashboard...")
            atualizar_ciclo("status", "pausado")
            time.sleep(10)
            estado = carregar_estado()
            if estado.get("parar_bot"):
                break

        feitas = estado.get("batalhas_feitas", 0)

        if feitas >= max_batalhas:
            log.info(f"✅ Limite de {max_batalhas} batalhas atingido!")
            atualizar_ciclo("status", "raffle")
            fazer_raffle_e_sair(client)
            log.info("Limpando dados da sessão encerrada...")
            resetar_dados_sessao_bg()
            atualizar_ciclo("status", "concluido")
            break

        # Verifica limite diário (sempre 100/dia independente do modo)
        sessao = estado.get("sessao_bg", {})
        restantes_hoje = sessao.get("restantes_hoje", 100)
        if restantes_hoje <= 0:
            log.info("⏸ Limite diário de 100 batalhas atingido — aguardando próximo dia...")
            atualizar_ciclo("status", "limite_diario")
            # Aguarda 1h e verifica se resetou (sem zerar dados - sessão ainda ativa)
            time.sleep(3600)
            continue

        restantes = max_batalhas - feitas
        log.info(f"\n⚔ [BG] Batalha {feitas+1}/{max_batalhas} ({restantes} restantes)")

        # Relê sessão do servidor para pegar combates feitos manualmente
        try:
            soup_sess = client.get_full("/battleground/currentbattle/")
            sessao_atual = parsear_sessao_bg(soup_sess)
            if sessao_atual:
                _falhas_sessao_consec = 0
                estado["sessao_bg"] = sessao_atual
                salvar_estado(estado)
                restantes_hoje = sessao_atual.get("restantes_hoje", 100)
                if restantes_hoje <= 0:
                    log.info("Limite diário atingido — encerrando.")
                    atualizar_ciclo("status", "limite_diario")
                    time.sleep(3600)
                    continue
                log.info(f"  Sessão: hoje={sessao_atual.get('batalhas_dia',0)}/100 | restantes={restantes_hoje}")
            else:
                _falhas_sessao_consec += 1
                log.warning(f"  /currentbattle/ sem dados de sessão ({_falhas_sessao_consec}/2) — sessão pode ter encerrado")
                if _falhas_sessao_consec >= 2:
                    log.warning("loop_bg: sessão BG encerrada — completando raffle antes de sair")
                    atualizar_ciclo("status", "raffle")
                    fazer_raffle_e_sair(client, soup_sess)
                    resetar_dados_sessao_bg()
                    atualizar_ciclo("status", "concluido")
                    break
        except Exception as e:
            log.debug(f"  Não foi possível reler sessão: {e}")

        # Tenta encontrar e atacar um alvo
        combates = carregar_combates()
        alvo_encontrado = False

        ef_minha = eu.get("ef", 2.0)

        # Estratégia de busca: começa em minha_EF+5, baixa 0.5 até minha_EF-2
        # Entre candidatos com score>=70, sempre escolhe maior EF
        # Busca: começa em minha_EF+5, desce 0.5 por vez até minha_EF
        # Em cada passo busca UMA janela de 0.5 exata
        # Se achar score>=70 ataca o de maior EF entre eles
        # Se chegar em minha_EF sem achar ninguém com 70%, ataca o melhor % disponível
        # Busca: começa em minha_EF+5, desce 0.5 por vez até minha_EF
        # Cada nível tenta 3x antes de descer
        # Ao chegar na minha_EF: tenta 60%, depois 50%, depois maior %
        ef_topo      = round(ef_minha + EF_OFFSET_MAX, 1)
        ef_busca     = ef_topo
        melhor       = None
        TENTATIVAS_POR_EF = 3

        def avaliar_lista(adv_list):
            for adv in adv_list:
                av = avaliar_adversario_bg(adv, eu, combates)
                adv["_score"] = av["score"]
                adv["_rec"]   = av["recomendacao"]
            return adv_list

        def melhor_com_score(adv_list, score_min):
            ok = [a for a in adv_list if a["_score"] >= score_min]
            if ok:
                return max(ok, key=lambda a: (a.get("ef", 0), a["_score"]))
            return None

        # Fase 1: percorre EF+5 até EF, 3 tentativas por nível, procura 60%
        while ef_busca >= ef_minha and not melhor:
            ef_offset = round(ef_busca - ef_minha, 1)
            encontrou_alguem = False

            for tentativa in range(1, TENTATIVAS_POR_EF + 1):
                adversarios = buscar_adversarios(client, eu, 0.5, ef_offset)
                if adversarios:
                    avaliar_lista(adversarios)
                    encontrou_alguem = True
                    candidato = melhor_com_score(adversarios, SCORE_MIN_ATACAR)
                    if candidato:
                        melhor = candidato
                        log.info(f"  ✓ EF{ef_busca:.1f} (t{tentativa}): {melhor['nome']} EF{melhor.get('ef',0)} Score:{melhor['_score']}")
                        break
                    else:
                        log.info(f"  EF{ef_busca:.1f} t{tentativa}/{TENTATIVAS_POR_EF}: {len(adversarios)} encontrados, nenhum >={SCORE_MIN_ATACAR}%")
                else:
                    log.info(f"  EF{ef_busca:.1f} t{tentativa}/{TENTATIVAS_POR_EF}: nenhum adversário")

                if not melhor and tentativa < TENTATIVAS_POR_EF:
                    time.sleep(2)

            if not melhor:
                ef_busca = round(ef_busca - 0.5, 1)
                time.sleep(1)

        # Fase 2: chegou na minha_EF sem achar 70% — tenta 60%, 50%, depois maior %
        if not melhor:
            log.info(f"  Sem 70%+ em nenhuma EF — buscando na minha EF com critério menor...")
            ef_offset_base = 0.0  # minha EF exata

            for score_fallback in [60, 50]:
                for tentativa in range(1, TENTATIVAS_POR_EF + 1):
                    adversarios = buscar_adversarios(client, eu, 0.5, ef_offset_base)
                    if adversarios:
                        avaliar_lista(adversarios)
                        candidato = melhor_com_score(adversarios, score_fallback)
                        if candidato:
                            melhor = candidato
                            log.info(f"  [FALLBACK {score_fallback}%] t{tentativa}: {melhor['nome']} EF{melhor.get('ef',0)} Score:{melhor['_score']}")
                            break
                        else:
                            log.info(f"  Fallback {score_fallback}% t{tentativa}/{TENTATIVAS_POR_EF}: nenhum >={score_fallback}%")
                    else:
                        log.info(f"  Fallback {score_fallback}% t{tentativa}/{TENTATIVAS_POR_EF}: nenhum adversário")
                    if not melhor and tentativa < TENTATIVAS_POR_EF:
                        time.sleep(2)
                if melhor:
                    break

            # Último recurso: ataca o de maior % disponível
            if not melhor:
                for tentativa in range(1, TENTATIVAS_POR_EF + 1):
                    adversarios = buscar_adversarios(client, eu, 0.5, ef_offset_base)
                    if adversarios:
                        avaliar_lista(adversarios)
                        melhor = max(adversarios, key=lambda a: (a["_score"], a.get("ef", 0)))
                        log.info(f"  [MAIOR %] t{tentativa}: {melhor['nome']} EF{melhor.get('ef',0)} Score:{melhor['_score']}")
                        break
                    time.sleep(2)

        # Executa ataque se achou alvo
        if melhor:
            # Verifica guild do adversário antes de atacar
            meu_clan = eu.get("meu_clan_id")
            if meu_clan:
                try:
                    soup_adv = client.get_url(f"{BASE_URL}/player/{melhor.get('user_id', '')}/")
                    clan_adv = _clan_id_de_perfil_bg(soup_adv)
                    if clan_adv == meu_clan:
                        log.warning(f"  {melhor['nome']} é da mesma guild — pulando!")
                        melhor = None
                except Exception:
                    pass

        if melhor:
            atualizar_ciclo("alvo_atual", {
                "nome": melhor.get("nome"),
                "ef":   melhor.get("ef"),
                "score": melhor["_score"],
                "tipo": melhor.get("tipo"),
            })

            soup_result = atacar(client, melhor)
            resultado, gold, xp, dano_caus, dano_rec, turnos_stats = parsear_resultado_combate(soup_result)

            log.info(f"  Resultado: {resultado.upper()} | Gold: {gold} | XP: {xp} | "
                     f"Dano: {dano_caus} causado / {dano_rec} recebido")

            registrar_combate(eu, melhor, resultado, gold, xp, dano_caus, dano_rec, turnos=turnos_stats)

            estado["batalhas_feitas"] = feitas + 1
            if resultado == "vitoria":
                estado["vitorias"]   = estado.get("vitorias", 0) + 1
                estado["gold_total"] = estado.get("gold_total", 0) + gold
                estado["xp_total"]   = estado.get("xp_total", 0) + xp
            else:
                estado["derrotas"] = estado.get("derrotas", 0) + 1
            estado["ultimo_ataque"] = agora().isoformat()
            salvar_estado(estado)

            insights = calcular_insights(carregar_combates())
            atualizar_ciclo("estado", estado)
            atualizar_ciclo("insights", insights)
            atualizar_ciclo("ultimo_combate", {
                "nome":      melhor.get("nome"),
                "ef":        melhor.get("ef"),
                "resultado": resultado,
                "gold":      gold,
                "xp":        xp,
                "score":     melhor["_score"],
                "score_sim": melhor.get("_score_sim", melhor["_score"]),
                "tipo":      melhor.get("tipo"),
                "timestamp": agora().isoformat(),
            })
            alvo_encontrado = True

        if not alvo_encontrado:
            # Verifica se é CD ou realmente sem alvos
            try:
                soup_cd = client.get("/raubzug/")
                txt = soup_cd.get_text()
                if "Apenas pode efetuar" in txt or "minutos" in txt.lower():
                    m = __import__('re').search(r'var Secondscounter\s*=\s*(\d+)', str(soup_cd))
                    if m:
                        cd_seg = int(m.group(1)) + 5
                        log.info(f"  Personagem em CD — aguardando {fmt_t(cd_seg)}")
                        atualizar_ciclo("proximo_ataque", (agora()+timedelta(seconds=cd_seg)).isoformat())
                        time.sleep(cd_seg)
                        continue
            except: pass
            log.warning("  Sem alvos viáveis após todas tentativas. Aguardando próximo ciclo...")
            atualizar_ciclo("status", "sem_alvo")

        # Aguarda cooldown
        proximo = agora() + timedelta(seconds=cooldown)
        log.info(f"  💤 Próximo ataque em {fmt_t(cooldown)} ({proximo:%H:%M:%S})")
        atualizar_ciclo("proximo_ataque", proximo.isoformat())
        time.sleep(cooldown)

# ═══════════════════════════════════════════════════════════════
# SERVIDOR DASHBOARD
# ═══════════════════════════════════════════════════════════════
def iniciar_servidor_bg(porta):
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json as _json

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def handle_error(self, request, client_address): pass  # suprime ConnectionAbortedError no log

        def do_GET(self):
            if self.path in ("/dados", "/dados/"):
                ciclo = carregar_json(CICLO_FILE, {})
                try:
                    combates = carregar_combates()
                    ciclo["historico"] = combates[-20:]
                except: pass
                data = _json.dumps(ciclo, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)

            elif self.path in ("/combates", "/combates/"):
                data = _json.dumps(carregar_combates(), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)

            elif self.path in ("/log", "/log/"):
                try:
                    txt = LOG_FILE.read_text(encoding="utf-8")
                    linhas = txt.strip().split("\n")[-50:]
                    data = "\n".join(linhas).encode("utf-8")
                except: data = b""
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)

            elif self.path in ("/api/parar", "/api/parar/"):
                estado_p = carregar_estado()
                estado_p["parar_bot"] = True
                salvar_estado(estado_p)
                data = b'{"ok": true, "msg": "Sinal de parada enviado"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)

            elif self.path in ("/api/pausar", "/api/pausar/"):
                estado_p = carregar_estado()
                pausado = not estado_p.get("pausado", False)
                estado_p["pausado"] = pausado
                salvar_estado(estado_p)
                msg = "pausado" if pausado else "retomado"
                data = f'{{"ok": true, "pausado": {str(pausado).lower()}, "msg": "Bot {msg}"}}'.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)

            elif self.path in ("/dashboard", "/dashboard/"):
                try:
                    # SCRIPT_DIR é resolvido antes do os.chdir()
                    dash_path = SCRIPT_DIR / "dashboard_bg.html"
                    if not dash_path.exists():
                        dash_path = WORKDIR / "dashboard_bg.html"
                    html = dash_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(html)
                except:
                    self.send_response(404)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("", porta), Handler)
    server.allow_reuse_address = True  # Evita TIME_WAIT bloqueando a porta
    import socket
    server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    log.info(f"Dashboard BG: http://localhost:{porta}/dashboard")
    server.serve_forever()

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KnightFight BattleGround Bot")
    parser.add_argument("--workdir", help="Pasta de trabalho do perfil")
    parser.add_argument("--modo",    default="free", choices=["free","medio","premium"])
    args = parser.parse_args()

    # Define workdir — sempre usa path absoluto
    if args.workdir:
        WORKDIR = Path(args.workdir).resolve()
    else:
        WORKDIR = Path(".").resolve()

    WORKDIR.mkdir(parents=True, exist_ok=True)
    os.chdir(WORKDIR)

    # Redefine paths com absoluto
    STATE_FILE    = WORKDIR / "bg_estado.json"
    COMBATES_FILE = WORKDIR / "bg_combates.json"
    CICLO_FILE    = WORKDIR / "bg_ciclo.json"
    LOG_FILE      = WORKDIR / "bot_bg.log"

    init_log_file()

    # Carrega config
    config_path = WORKDIR / "config_bg.json"
    if config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        servidor       = cfg.get("servidor", cfg.get("server", "int7"))
        BASE_URL       = f"https://{servidor}.knightfight.moonid.net"
        COOKIES_RAW    = cfg.get("cookies", COOKIES_RAW)
        MY_USER_ID     = cfg.get("userid", MY_USER_ID)
        DASHBOARD_PORT = cfg.get("port", DASHBOARD_PORT)
        MODO_BG        = cfg.get("modo", args.modo)
        if cfg.get("ef_offset_max") is not None:
            globals()["EF_OFFSET_MAX"] = float(cfg["ef_offset_max"])
        print(f"Config BG carregada:")
        print(f"  Perfil : {cfg.get('perfil', cfg.get('profile','?'))}")
        print(f"  Servidor: {servidor} -> {BASE_URL}")
        print(f"  UserID : {MY_USER_ID}")
        print(f"  Porta  : {DASHBOARD_PORT}")
        print(f"  Modo   : {MODO_BG}")
        print(f"  Alignment: {cfg.get('alignment', 'light')}")
        print(f"  Cookies: {'OK' if COOKIES_RAW and COOKIES_RAW != 'COLE_SEUS_COOKIES_AQUI' else 'FALTANDO!'}")
    else:
        MODO_BG = args.modo
        # Tenta usar config.json normal como fallback
        cfg_normal = WORKDIR / "config.json"
        if cfg_normal.exists():
            cfg = json.loads(cfg_normal.read_text(encoding="utf-8"))
            servidor   = cfg.get("servidor", cfg.get("server", "int7"))
            BASE_URL   = f"https://{servidor}.knightfight.moonid.net"
            COOKIES_RAW  = cfg.get("cookies", COOKIES_RAW)
            MY_USER_ID   = cfg.get("userid", MY_USER_ID)
            DASHBOARD_PORT = cfg.get("port", DASHBOARD_PORT) + 1
            print(f"⚠ config_bg.json não encontrado — usando config.json normal")
            print(f"  Servidor: {servidor} | Porta BG: {DASHBOARD_PORT}")
            print(f"  Cookies: {'OK' if COOKIES_RAW and COOKIES_RAW != 'COLE_SEUS_COOKIES_AQUI' else 'FALTANDO!'}")
        else:
            print(f"⚠ Nenhuma config encontrada — usando padrão (vai falhar!)")

    # Inicia servidor dashboard
    t = threading.Thread(target=iniciar_servidor_bg, args=(DASHBOARD_PORT,), daemon=True)
    t.start()

    # Cria cliente (uma única vez — reutilizado em todas as sessões BG)
    client = ClienteBG(BASE_URL, COOKIES_RAW)

    alignment          = cfg.get("alignment", "light")           if "cfg" in dir() else "light"
    retry_sem_equip_seg = int(cfg.get("retry_sem_equip_seg", 1800)) if "cfg" in dir() else 1800

    log.info("=" * 50)
    log.info(f"KnightFight BG Bot | Modo: {MODOS_BG[MODO_BG]['nome']}")
    log.info(f"Dashboard: http://localhost:{DASHBOARD_PORT}/dashboard")
    log.info("=" * 50)

    # ══════════════════════════════════════════════════════════════
    # LOOP ETERNO — entrar → batalhas → raffle → cooldown → repetir
    # Decisões locais primeiro: zero requests desnecessários
    # ══════════════════════════════════════════════════════════════
    eu = {}

    def _checar_parar():
        """Levanta KeyboardInterrupt se parar_bot estiver setado."""
        est = carregar_estado()
        if est.get("parar_bot"):
            log.info("🛑 Bot parado pelo dashboard")
            est.pop("parar_bot", None)
            salvar_estado(est)
            raise KeyboardInterrupt

    def _nivel_local():
        """Lê o nível do personagem sem fazer request.
        Prefere estado.json (bot principal, sempre atualizado) sobre bg_estado.json."""
        nivel_bg = carregar_estado().get("level", 0)
        nivel_bot = carregar_json(WORKDIR / "estado.json", {}).get("level", 0)
        return max(nivel_bg, nivel_bot)

    def _cooldown_restante_local():
        """
        Lê bg_ciclo.json sem request.
        Retorna segundos restantes se ainda em cooldown, 0 caso contrário.
        """
        try:
            ciclo = carregar_json(CICLO_FILE, {})
            if ciclo.get("status") == "cooldown_bg":
                proximo = ciclo.get("proximo_bg") or ciclo.get("proximo_ataque")
                if proximo:
                    dt_alvo = datetime.fromisoformat(proximo)
                    restante = (dt_alvo - datetime.now()).total_seconds()
                    if restante > 0:
                        return int(restante)
        except Exception:
            pass
        return 0

    try:
        while True:
            _checar_parar()

            # ── 1. Nível local (sem request) ──────────────────────
            nivel = _nivel_local()
            if nivel and nivel < 10:
                # Cache pode estar stale — confirma ao vivo antes de bloquear
                try:
                    eu_chk = parsear_status_bg(client.get_full("/status/"))
                    nivel_fresh = eu_chk.get("level", nivel)
                    if nivel_fresh != nivel:
                        est_chk = carregar_estado()
                        est_chk["level"] = nivel_fresh
                        salvar_estado(est_chk)
                        log.info(f"  Nível atualizado: {nivel} → {nivel_fresh}")
                        nivel = nivel_fresh
                except Exception as _e:
                    log.warning(f"  Erro ao verificar nível ao vivo: {_e}")
            if nivel and nivel < 10:
                log.info(f"  Nível {nivel} < 10 — BG requer Lv10. Aguardando 1h...")
                atualizar_ciclo("status", "aguardando_nivel")
                if not _dormir_fatias(3600):
                    raise KeyboardInterrupt
                continue

            # ── 2. Cooldown local (sem request) ───────────────────
            cd_seg = _cooldown_restante_local()
            if cd_seg > 0:
                ciclo = carregar_json(CICLO_FILE, {})
                proximo_str = ciclo.get("proximo_bg") or ciclo.get("proximo_ataque", "")
                try:
                    dt_alvo = datetime.fromisoformat(proximo_str)
                    log.info(f"  Cooldown BG (cache) — próxima entrada às {dt_alvo:%d/%m %H:%M:%S} ({fmt_t(cd_seg)})")
                except Exception:
                    log.info(f"  Cooldown BG (cache) — {fmt_t(cd_seg)} restantes")
                atualizar_ciclo("status", "cooldown_bg")
                if not _dormir_fatias(cd_seg):
                    raise KeyboardInterrupt
                log.info("  Cooldown encerrado — verificando sessão...")
                continue

            # ── 3. Verifica sessão ativa (1 request) ─────────────
            log.info("Verificando sessão BG ativa...")
            sessao = {}
            estado = carregar_estado()
            _soup_sessao = None
            try:
                _soup_sessao = client.get_full("/battleground/currentbattle/")
                sessao = parsear_sessao_bg(_soup_sessao)
                atualizar_ciclo("sessao", sessao)
                estado["sessao_bg"] = sessao
                salvar_estado(estado)
            except Exception as e:
                log.warning(f"Erro ao verificar sessão: {e}")

            sessao_ativa = sessao.get("batalhas_total") is not None or \
                           sessao.get("batalhas_feitas") is not None

            if sessao_ativa:
                # Sessão já ativa — sincroniza stats e vai às batalhas
                log.info("Sessão BG ativa detectada — sincronizando stats...")
                try:
                    eu = parsear_status_bg(client.get_full("/status/"))
                    log.info(f"  Personagem: Lv{eu.get('level','?')} | EF {eu.get('ef','?')} | "
                             f"AC {eu.get('arte_combate','?')} Blq {eu.get('bloqueio','?')}")
                    # Salva level no estado para futuras checagens locais
                    estado["level"] = eu.get("level", 0)
                    atualizar_ciclo("eu", eu)
                except Exception as e:
                    log.warning(f"  Erro ao coletar status: {e}")

                try:
                    stats_reais = parsear_estatisticas_bg(client.get_full("/battleground/statistics/"))
                    estado["batalhas_feitas"] = stats_reais.get("batalhas", sessao.get("batalhas_dia", 0))
                    if stats_reais.get("vitorias") is not None: estado["vitorias"]   = stats_reais["vitorias"]
                    if stats_reais.get("derrotas") is not None: estado["derrotas"]   = stats_reais["derrotas"]
                    if stats_reais.get("gold")     is not None: estado["gold_total"] = stats_reais["gold"]
                    log.info(f"  Stats: {stats_reais.get('batalhas',0)} batalhas | "
                             f"{stats_reais.get('vitorias',0)}V/{stats_reais.get('derrotas',0)}D | "
                             f"{stats_reais.get('gold',0)}g")
                except Exception: pass

                eu["sessao_inicio"]    = sessao.get("inicio", f"sess_{datetime.now():%Y%m%d}")
                estado["sessao_bg_id"] = eu["sessao_inicio"]
                salvar_estado(estado)

            else:
                # ── 4. Sem sessão — verifica nível antes de tentar entrar ──
                # Verifica raffle pendente antes de tentar entrar
                # (sessão encerrada pelo servidor mas start_raffle ainda não clicado)
                if _soup_sessao is not None and _soup_sessao.find("input", {"name": "start_raffle"}):
                    log.info("Raffle pendente detectado — completando raffle antes de re-entrar no BG")
                    atualizar_ciclo("status", "raffle")
                    fazer_raffle_e_sair(client, _soup_sessao)
                    time.sleep(5)
                    continue

                log.info("Sem sessão BG ativa — tentando entrar...")
                atualizar_ciclo("status", "aguardando_entrada")
                _checar_parar()

                # Se nível ainda desconhecido (0), busca via /status/ agora
                nivel_atual = _nivel_local()
                if nivel_atual == 0:
                    try:
                        eu_tmp = parsear_status_bg(client.get_full("/status/"))
                        nivel_atual = eu_tmp.get("level", 0)
                        estado2 = carregar_estado()
                        estado2["level"] = nivel_atual
                        salvar_estado(estado2)
                        log.info(f"  Nível obtido: {nivel_atual}")
                    except Exception as e:
                        log.warning(f"  Erro ao obter nível: {e}")

                if 0 < nivel_atual < 10:
                    log.info(f"  Nível {nivel_atual} < 10 — BG requer Lv10. Aguardando 1h...")
                    atualizar_ciclo("status", "aguardando_nivel")
                    if not _dormir_fatias(3600):
                        raise KeyboardInterrupt
                    continue

                status_entrada, wait_extra = entrar_bg(client, MODO_BG, alignment)

                if status_entrada == "em_sessao":
                    # BG já estava ativo — re-busca sessão e prossegue normalmente
                    log.info("  BG já em sessão — re-sincronizando...")
                    atualizar_ciclo("status", "em_sessao")
                    try:
                        s_re = parsear_sessao_bg(client.get_full("/battleground/currentbattle/"))
                        estado["sessao_bg"] = s_re
                        atualizar_ciclo("sessao", s_re)
                        eu["sessao_inicio"] = s_re.get("inicio", f"sess_{datetime.now():%Y%m%d}")
                        estado["sessao_bg_id"] = eu["sessao_inicio"]
                        salvar_estado(estado)
                    except Exception as e_re:
                        log.warning(f"  Erro ao re-sincronizar sessão: {e_re}")
                        eu.setdefault("sessao_inicio", f"sess_{datetime.now():%Y%m%d}")
                    # Deixa cair no bloco de execução de batalhas abaixo

                elif status_entrada == "ok":
                    log.info("  Entrada confirmada — aguardando 5s para sessão ser registrada...")
                    time.sleep(5)
                    try:
                        s2 = parsear_sessao_bg(client.get_full("/battleground/currentbattle/"))
                        if s2.get("batalhas_total") is not None or s2.get("batalhas_feitas") is not None:
                            eu["sessao_inicio"]    = s2.get("inicio", f"sess_{datetime.now():%Y%m%d}")
                            estado["sessao_bg"]    = s2
                            estado["sessao_bg_id"] = eu["sessao_inicio"]
                            estado["batalhas_feitas"] = s2.get("batalhas_feitas", 0)
                            salvar_estado(estado)
                            log.info(f"  Sessão: {s2.get('batalhas_feitas',0)}/{s2.get('batalhas_total','?')} batalhas")
                        else:
                            eu.setdefault("sessao_inicio", f"sess_{datetime.now():%Y%m%d}")
                    except Exception as e2:
                        log.warning(f"  Erro ao confirmar sessão: {e2}")
                        eu.setdefault("sessao_inicio", f"sess_{datetime.now():%Y%m%d}")
                    # Sincroniza status/stats após entrada bem-sucedida
                    try:
                        eu.update(parsear_status_bg(client.get_full("/status/")))
                        estado["level"] = eu.get("level", 0)
                        atualizar_ciclo("eu", eu)
                        salvar_estado(estado)
                    except Exception: pass

                elif status_entrada == "cooldown":
                    dt_alvo = datetime.now() + timedelta(seconds=wait_extra)
                    log.info(f"  Cooldown BG — próxima entrada às {dt_alvo:%d/%m %H:%M:%S} ({fmt_t(wait_extra)})")
                    atualizar_ciclo("status", "cooldown_bg")
                    atualizar_ciclo("proximo_bg", dt_alvo.isoformat())
                    if not _dormir_fatias(wait_extra):
                        raise KeyboardInterrupt
                    log.info("  Cooldown encerrado — retentando...")
                    continue  # volta ao topo — vai passar pelo check local primeiro

                elif status_entrada == "nivel_insuficiente":
                    log.info(f"  Nível insuficiente para BG (Lv10 requerido). Aguardando 1h...")
                    atualizar_ciclo("status", "aguardando_nivel")
                    # Tenta salvar o nível atual para evitar request na próxima iteração
                    try:
                        eu_tmp = parsear_status_bg(client.get_full("/status/"))
                        estado["level"] = eu_tmp.get("level", 0)
                        salvar_estado(estado)
                        eu.update(eu_tmp)
                        atualizar_ciclo("eu", eu)
                    except Exception: pass
                    if not _dormir_fatias(3600):
                        raise KeyboardInterrupt
                    continue

                elif status_entrada == "sem_equipamento":
                    log.info(f"  Sem equipamento adequado — aguardando {fmt_t(retry_sem_equip_seg)}...")
                    atualizar_ciclo("status", "aguardando_equipamento")
                    if not _dormir_fatias(retry_sem_equip_seg):
                        raise KeyboardInterrupt
                    continue

                else:  # "falha" — nenhum modo disponível (sem equipamento / cookie expirado)
                    log.error("❌ Falha ao entrar no BG — verifique cookies e configuração.")
                    atualizar_ciclo("status", "erro_entrada")
                    log.info("  Aguardando 30min antes de retentar...")
                    if not _dormir_fatias(1800):
                        raise KeyboardInterrupt
                    continue

            # ── 5. Executa batalhas da sessão ─────────────────────
            atualizar_ciclo("status", "em_sessao")
            log.info(f"\n{'='*50}")
            log.info(f"Iniciando sessão BG — Modo: {MODOS_BG[MODO_BG]['nome']}")
            log.info(f"{'='*50}")
            try:
                loop_bg(client, eu, MODO_BG)
            except Exception as e_loop:
                log.error(f"Erro inesperado no loop BG: {type(e_loop).__name__}: {e_loop}")
                log.info("Aguardando 10min antes de reiniciar...")
                atualizar_ciclo("status", "erro_loop")
                time.sleep(600)
                continue

            # ── 6. Sessão concluída — volta ao topo ───────────────
            log.info("Sessão BG encerrada — retornando ao ciclo...")
            time.sleep(5)

    except KeyboardInterrupt:
        log.info("Bot BG encerrado pelo usuário.")
