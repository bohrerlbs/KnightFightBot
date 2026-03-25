# ═══════════════════════════════════════════════════════════════
# KnightFight — BattleGround Bot v1.0.0
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
MODO_BG = "free"

# Estratégia de busca
EF_RANGE_ACIMA   = 2.0   # busca EF nossa até +2.0
EF_RANGE_FALLBACK = 1.0  # se não achar bom, tenta com +1.0
SCORE_MIN_ATACAR = 70    # score mínimo para atacar (simulador preciso)
MAX_REBUSCAS     = 3     # tentativas de busca antes de desistir do ciclo

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
log.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_sh  = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)
log.addHandler(_sh)

def init_log_file():
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(_fmt)
    log.addHandler(fh)

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

# ═══════════════════════════════════════════════════════════════
# PARSERS
# ═══════════════════════════════════════════════════════════════
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
    """Extrai info da sessão atual do BG."""
    sessao = {}
    try:
        texto = soup.get_text(" ", strip=True)

        # Pega todos os "X de um máximo de Y"
        matches = re.findall(r"(\d+)\s+de\s+um\s+m[aá]ximo\s+de\s+(\d+)", texto)
        for val_s, max_s in matches:
            val, maximo = int(val_s), int(max_s)
            if maximo == 100:
                # Limite diário (sempre 100, independente do modo)
                sessao["batalhas_dia"]     = val
                sessao["batalhas_dia_max"] = maximo
            elif maximo in (200, 400):
                # Total da sessão (modo médio=200, premium=400)
                sessao["batalhas_total"]   = maximo
                sessao["batalhas_feitas"]  = val

        # Se modo free (max=100), total = dia
        if "batalhas_total" not in sessao and "batalhas_dia" in sessao:
            sessao["batalhas_total"]  = sessao["batalhas_dia_max"]
            sessao["batalhas_feitas"] = sessao["batalhas_dia"]

        # Restante do dia
        if "batalhas_dia" in sessao:
            sessao["restantes_hoje"] = sessao["batalhas_dia_max"] - sessao["batalhas_dia"]

        # Datas
        m = re.search(r"Inicio da sessão[^:]*:\s*([\d.]+\s[\d:]+)", texto)
        if m: sessao["inicio"] = m.group(1)
        m = re.search(r"Fim da sessão[^:]*:\s*([\d.]+\s[\d:]+)", texto)
        if m: sessao["fim"] = m.group(1)
        # Limite diário: "pode realizar mais X ataques"
        m = re.search(r"pode realizar mais\s+(\d+)\s+ataques", texto)
        if m: sessao["restantes_hoje"] = int(m.group(1))

    except Exception as e:
        log.warning(f"parsear_sessao_bg: {e}")
    return sessao

def parsear_estatisticas_bg(soup):
    """Extrai estatísticas da sessão BG."""
    stats = {}
    try:
        tds = soup.find_all("td", class_="tdn")
        i = 0
        while i < len(tds) - 1:
            label = tds[i].get_text(strip=True).lower()
            valor = tds[i+1].get_text(strip=True)
            m = re.search(r"[\d.]+", valor.replace(".", "").replace(",", ""))
            if m:
                v = int(m.group())
                if "ofensiva" in label: stats["batalhas"] = v
                elif "vencido" in label: stats["vitorias"] = v
                elif "derrota" in label: stats["derrotas"] = v
                elif "empate" in label: stats["empates"] = v
                elif "batalha ganho" in label: stats["pontos_batalha"] = v
                elif "ouro ganho" in label: stats["gold"] = v
                elif "certeiro" in label and "atribuído" in label: stats["dano_causado"] = v
                elif "certeiro" in label and "sofrido" in label: stats["dano_recebido"] = v
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

def parsear_resultado_combate(soup):
    """Extrai resultado do combate BG."""
    resultado = "desconhecido"
    gold = 0
    xp = 0
    dano_causado = 0
    dano_recebido = 0
    try:
        for script in soup.find_all("script"):
            txt = script.string or ""
            if "displayFightReport" not in txt: continue
            m = re.search(r'"winner"\s*:\s*"(\w+)"', txt)
            if m:
                winner = m.group(1)
                resultado = "vitoria" if winner == "attacker" else "derrota"
            break

        html_txt = str(soup)
        m = re.search(r"(\d+)\s*<img[^>]*gold[^>]*>", html_txt)
        if m: gold = int(m.group(1))

        m_xp = re.findall(r"(\d+)\s*<img[^>]*exp_scroll[^>]*>", html_txt)
        for v in m_xp:
            if int(v) > 0: xp = int(v); break

        # Dano causado/recebido do log de combate
        danos_atk = re.findall(r'class="attacker"[^>]*>.*?(\d+[\.,]\d+)\s*points of damage', html_txt)
        danos_def = re.findall(r'class="defender"[^>]*>.*?(\d+[\.,]\d+)\s*points of damage', html_txt)
        dano_causado  = sum(float(d.replace(",",".")) for d in danos_atk)
        dano_recebido = sum(float(d.replace(",",".")) for d in danos_def)

    except Exception as e:
        log.warning(f"parsear_resultado_combate: {e}")
    return resultado, gold, xp, round(dano_causado,1), round(dano_recebido,1)

# ═══════════════════════════════════════════════════════════════
# SISTEMA DE APRENDIZADO
# ═══════════════════════════════════════════════════════════════
def registrar_combate(eu, adversario, resultado, gold, xp, dano_causado, dano_recebido):
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

    # ── 8. Ajuste por aprendizado (se tiver combates suficientes)
    if combates and len(combates) >= 10:
        # Busca combates similares (mesmo range de EF ±0.5)
        ef_adv = ef_d
        similares = [c for c in combates
                     if abs(c.get("adv_ef", 0) - ef_adv) <= 0.5]
        if len(similares) >= 3:
            wr_similar = sum(1 for c in similares if c["resultado"] == "vitoria") / len(similares)
            # Ajusta score pela experiência real (peso 30%)
            score_aprendido = wr_similar * 100
            score = round(score * 0.7 + score_aprendido * 0.3)
            log.debug(f"  Aprendizado: {len(similares)} combates similares → WR {wr_similar*100:.0f}% → ajuste")

    score = max(0, min(100, score))

    # ── Simulação de combate ──────────────────────────────────────────────────
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from combat_sim import simular_combate
        sim = simular_combate(eu, adv)
        sim_score = sim["score"]
        score = sim_score
        score = max(0, min(100, score))
        adv["_score_sim"] = sim_score  # salva para registro
    except Exception:
        pass

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


def loop_bg(client, eu, modo):
    """Loop principal do bot BG."""
    config_modo = MODOS_BG[modo]
    cooldown    = config_modo["cooldown"]
    max_batalhas = config_modo["batalhas"]

    log.info(f"🏟 Modo: {config_modo['nome']} | Cooldown: {fmt_t(cooldown)} | Max: {max_batalhas}")

    estado = carregar_estado()
    estado.setdefault("batalhas_feitas", 0)
    estado.setdefault("vitorias", 0)
    estado.setdefault("derrotas", 0)
    estado.setdefault("gold_total", 0)
    estado.setdefault("xp_total", 0)
    estado["modo"] = modo
    estado["eu"] = eu
    salvar_estado(estado)

    while True:
        estado = carregar_estado()
        feitas = estado.get("batalhas_feitas", 0)

        if feitas >= max_batalhas:
            log.info(f"✅ Limite de {max_batalhas} batalhas atingido! Bot encerrado.")
            atualizar_ciclo("status", "concluido")
            break

        # Verifica limite diário (sempre 100/dia independente do modo)
        sessao = estado.get("sessao_bg", {})
        restantes_hoje = sessao.get("restantes_hoje", 100)
        if restantes_hoje <= 0:
            log.info("Limite diário de 100 batalhas atingido — aguardando próximo dia...")
            atualizar_ciclo("status", "limite_diario")
            time.sleep(3600)  # aguarda 1h e verifica novamente
            continue

        restantes = max_batalhas - feitas
        log.info(f"\n⚔ [BG] Batalha {feitas+1}/{max_batalhas} ({restantes} restantes)")

        # Relê sessão do servidor para pegar combates feitos manualmente
        try:
            soup_sess = client.get_full("/battleground/currentbattle/")
            sessao_atual = parsear_sessao_bg(soup_sess)
            if sessao_atual:
                estado["sessao_bg"] = sessao_atual
                salvar_estado(estado)
                restantes_hoje = sessao_atual.get("restantes_hoje", 100)
                if restantes_hoje <= 0:
                    log.info("Limite diário de 100 batalhas atingido — aguardando...")
                    atualizar_ciclo("status", "limite_diario")
                    time.sleep(3600)
                    continue
                log.info(f"  Sessão: hoje={sessao_atual.get('batalhas_dia',0)}/100 | restantes={restantes_hoje}")
        except Exception as e:
            log.debug(f"  Não foi possível reler sessão: {e}")

        # Tenta encontrar e atacar um alvo
        combates = carregar_combates()
        alvo_encontrado = False

        ef_minha = eu.get("ef", 2.0)

        # Estratégia de busca: começa em minha_EF+5, baixa 0.5 até minha_EF-2
        # Entre candidatos com score>=70, sempre escolhe maior EF
        ef_topo  = ef_minha + 5.0
        ef_fundo = max(0.5, ef_minha - 2.0)
        ef_busca = ef_topo
        melhor   = None

        while ef_busca >= ef_fundo and not melhor:
            ef_offset = ef_busca - ef_minha
            adversarios = buscar_adversarios(client, eu, 0.5, ef_offset)

            if adversarios:
                for adv in adversarios:
                    av = avaliar_adversario_bg(adv, eu, combates)
                    adv["_score"]     = av["score"]
                    adv["_rec"]       = av["recomendacao"]
                    adv["_pontuacao"] = adv.get("ef", 0) * (av["score"] / 100)

                candidatos_ok = [a for a in adversarios if a["_score"] >= SCORE_MIN_ATACAR]
                if candidatos_ok:
                    melhor = max(candidatos_ok, key=lambda a: (a.get("ef", 0), a["_score"]))
                    log.info(f"  ✓ EF{ef_busca:.1f}: {melhor['nome']} EF{melhor.get('ef',0)} Score:{melhor['_score']}")
                else:
                    log.info(f"  EF{ef_busca:.1f}: {len(adversarios)} adversários, nenhum com score>={SCORE_MIN_ATACAR}")
            else:
                log.info(f"  EF{ef_busca:.1f}: nenhum adversário")

            if not melhor:
                ef_busca = round(ef_busca - 0.5, 1)
                time.sleep(1)

        # Se não achou ninguém com score>=70, busca na minha EF e ataca o melhor disponível
        if not melhor:
            log.info(f"  Nenhum alvo com score>={SCORE_MIN_ATACAR} encontrado — buscando melhor disponível na minha EF...")
            adversarios_fallback = buscar_adversarios(client, eu, 2.0, 0)
            if adversarios_fallback:
                for adv in adversarios_fallback:
                    av = avaliar_adversario_bg(adv, eu, combates)
                    adv["_score"] = av["score"]
                    adv["_rec"]   = av["recomendacao"]
                candidatos = sorted(adversarios_fallback, key=lambda a: (a.get("ef",0), a["_score"]), reverse=True)
                melhor = candidatos[0]
                log.info(f"  [FALLBACK] Melhor disponível: {melhor['nome']} EF{melhor.get('ef',0)} Score:{melhor['_score']}")

        # Executa ataque se achou alvo
        if melhor:
            atualizar_ciclo("alvo_atual", {
                "nome": melhor.get("nome"),
                "ef":   melhor.get("ef"),
                "score": melhor["_score"],
                "tipo": melhor.get("tipo"),
            })

            soup_result = atacar(client, melhor)
            resultado, gold, xp, dano_caus, dano_rec = parsear_resultado_combate(soup_result)

            log.info(f"  Resultado: {resultado.upper()} | Gold: {gold} | XP: {xp} | "
                     f"Dano: {dano_caus} causado / {dano_rec} recebido")

            registrar_combate(eu, melhor, resultado, gold, xp, dano_caus, dano_rec)

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
        print(f"Config BG carregada:")
        print(f"  Perfil : {cfg.get('perfil', cfg.get('profile','?'))}")
        print(f"  Servidor: {servidor} -> {BASE_URL}")
        print(f"  UserID : {MY_USER_ID}")
        print(f"  Porta  : {DASHBOARD_PORT}")
        print(f"  Modo   : {MODO_BG}")
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

    # Cria cliente
    client = ClienteBG(BASE_URL, COOKIES_RAW)

    # Coleta status do personagem no BG
    log.info("Coletando status no BG...")
    try:
        soup_status = client.get_full("/status/")
        eu = parsear_status_bg(soup_status)
        log.info(f"Personagem: Lv{eu.get('level','?')} | EF {eu.get('ef','?')} | "
                 f"AC {eu.get('arte_combate','?')} Blq {eu.get('bloqueio','?')}")
        atualizar_ciclo("eu", eu)

        # Coleta sessão atual
        soup_sessao = client.get_full("/battleground/currentbattle/")
        sessao = parsear_sessao_bg(soup_sessao)
        restantes = sessao.get("restantes_hoje", "?")
        log.info(f"Sessão: {sessao.get('batalhas_feitas',0)}/{sessao.get('batalhas_total','?')} batalhas | Hoje: {sessao.get('batalhas_dia',0)}/100 | Restantes: {restantes}")
        atualizar_ciclo("sessao", sessao)

        # Salva sessao no estado para verificação do limite diário
        estado = carregar_estado()
        estado["sessao_bg"] = sessao
        salvar_estado(estado)
        estado = carregar_estado()
        if sessao.get("batalhas_feitas"):
            estado["batalhas_feitas"] = sessao["batalhas_feitas"]
            salvar_estado(estado)

    except Exception as e:
        log.error(f"Erro ao coletar status: {e}")
        eu = {}

    log.info("=" * 50)
    log.info(f"KnightFight BG Bot | Modo: {MODOS_BG[MODO_BG]['nome']}")
    log.info(f"Dashboard: http://localhost:{DASHBOARD_PORT}/dashboard")
    log.info("=" * 50)

    # Inicia loop
    try:
        loop_bg(client, eu, MODO_BG)
    except KeyboardInterrupt:
        log.info("Bot encerrado pelo usuário.")
