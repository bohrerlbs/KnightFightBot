"""
KnightFight Bot — Launcher v1.0.5
"""
import os, sys, json, subprocess, threading, time, re, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
os.chdir(BASE_DIR)
PROFILES_DIR = BASE_DIR / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)

# ── Versão — lida do arquivo externo para funcionar com auto-update ───────────
def get_version():
    v = BASE_DIR / "VERSION"
    try:
        return v.read_text(encoding="utf-8").strip() if v.exists() else "1.0.0"
    except:
        return "1.0.0"

GITHUB_RAW  = "https://raw.githubusercontent.com/bohrerlbs/KnightFightBot/main"
LAUNCHER_PORT = 8764
running_bots  = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
def get_profiles():
    profiles = []
    # Limpa processos mortos do dicionário
    dead = [n for n, p in running_bots.items() if p.poll() is not None]
    for n in dead:
        del running_bots[n]

    dirs = sorted(PROFILES_DIR.iterdir()) if PROFILES_DIR.exists() else []
    for d in dirs:
        cfg_path = d / "config.json"
        if not cfg_path.exists():
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["_name"]     = d.name
            cfg["_running"]  = d.name in running_bots and running_bots[d.name].poll() is None
            cfg["_bg_running"] = f"BG_{d.name}" in running_bots and running_bots[f"BG_{d.name}"].poll() is None
            cfg["_log_tail"] = get_log_tail(d.name, 5)
            profiles.append(cfg)
        except:
            pass
    return profiles

def get_log_tail(name, lines=20):
    log = PROFILES_DIR / name / "bot.log"
    if not log.exists():
        return []
    try:
        with open(log, encoding="utf-8", errors="replace") as f:
            return [l.rstrip() for l in f.readlines()[-lines:]]
    except:
        return []

def get_profile_port(name):
    cfg = PROFILES_DIR / name / "config.json"
    try:
        return int(json.loads(cfg.read_text(encoding="utf-8")).get("port", 8765))
    except:
        return 8765

def start_bg_bot(name, modo="free"):
    import json as _json
    profile_dir = BASE_DIR / "profiles" / name.upper()
    if not profile_dir.exists():
        return {"ok": False, "error": f"Perfil {name} não encontrado"}
    bot_bg = BASE_DIR / "bot_bg.py"
    if not bot_bg.exists():
        return {"ok": False, "error": "bot_bg.py não encontrado"}
    bg_key = f"BG_{name}"
    if bg_key in running_bots and running_bots[bg_key].poll() is None:
        return {"ok": False, "error": "BG Bot já rodando para este perfil"}
    # Sempre parte do config.json normal (tem cookies, servidor, userid)
    cfg_normal = profile_dir / "config.json"
    if cfg_normal.exists():
        cfg = _json.loads(cfg_normal.read_text(encoding="utf-8"))
    else:
        cfg = {}
    # Porta BG = porta normal + 5 (evita conflito)
    porta_normal = cfg.get("port", 8765)
    cfg["port"] = porta_normal + 5
    cfg["modo"] = modo
    cfg["perfil"] = name
    # Salva config_bg.json na pasta do perfil
    cfg_path = profile_dir / "config_bg.json"
    cfg_path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    log_file = profile_dir / "bot_bg.log"
    err_file = profile_dir / "bot_bg_err.log"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
    try:
        # Usa paths absolutos para evitar problemas de cwd
        log_file_abs = profile_dir.resolve() / "bot_bg.log"
        bot_bg_abs   = bot_bg.resolve()
        workdir_abs  = profile_dir.resolve()

        # Escreve header no log antes de iniciar
        import datetime as _dt
        with open(str(log_file_abs), "a", encoding="utf-8") as f:
            f.write(f"\n=== BG Bot {_dt.datetime.now():%H:%M:%S} ===\n")
            f.write(f"bot_bg: {bot_bg_abs} exists={bot_bg_abs.exists()}\n")
            f.write(f"workdir: {workdir_abs} exists={workdir_abs.exists()}\n")
            f.write(f"python: {sys.executable}\n")

        log_f = open(str(log_file_abs), "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-u", str(bot_bg_abs), "--workdir", str(workdir_abs)],
            stdout=log_f,
            stderr=log_f,
            env=env,
            cwd=str(BASE_DIR),
        )
        running_bots[bg_key] = proc
        return {"ok": True, "pid": proc.pid, "port": cfg.get("port", 8770)}
    except Exception as e:
        return {"ok": False, "error": f"Erro ao iniciar processo: {e}"}

def stop_bg_bot(name):
    bg_key = f"BG_{name}"
    if bg_key in running_bots:
        p = running_bots.pop(bg_key)
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except: p.kill()
        return {"ok": True}
    return {"ok": False, "error": "BG Bot não estava rodando"}

def status_bg_bot(name):
    bg_key = f"BG_{name}"
    running = bg_key in running_bots and running_bots[bg_key].poll() is None
    return {"running": running}

def start_bot(name):
    if name in running_bots and running_bots[name].poll() is None:
        return {"ok": False, "error": "Já está rodando"}
    bot_py = BASE_DIR / "bot.py"
    if not bot_py.exists():
        return {"ok": False, "error": "bot.py não encontrado na pasta " + str(BASE_DIR)}
    port = get_profile_port(name)
    profile_dir = PROFILES_DIR / name
    log_path = profile_dir / "bot.log"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    try:
        p = subprocess.Popen(
            [sys.executable, str(bot_py), "--workdir", str(profile_dir)],
            cwd=str(BASE_DIR),
            stdout=open(log_path, "a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            env=env
        )
        running_bots[name] = p
        return {"ok": True, "port": port}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def stop_bot(name):
    p = running_bots.get(name)
    if not p or p.poll() is not None:
        return {"ok": False, "error": "Não está rodando"}
    try:
        p.terminate()
        try: p.wait(timeout=5)
        except: p.kill()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def save_profile(data):
    name = re.sub(r'[^\w\-]', '_', data.get("name", "novo")).lower()
    path = PROFILES_DIR / name
    path.mkdir(exist_ok=True)
    port = int(data.get("port") or 8765 + len(get_profiles()))
    cfg  = {
        "profile": name,
        "server":  data.get("server", "int7"),
        "userid":  data.get("userid", ""),
        "cookies": data.get("cookies", ""),
        "port":    port,
    }
    (path / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    bat = f'@echo off\ncd /d "{BASE_DIR}"\npython bot.py --profile {name}\npause\n'
    (BASE_DIR / f"iniciar_{name}.bat").write_text(bat)
    return {"ok": True, "name": name, "config": cfg}

def delete_profile(name):
    import shutil
    path = PROFILES_DIR / name
    if path.exists():
        shutil.rmtree(path)
    bat = BASE_DIR / f"iniciar_{name}.bat"
    if bat.exists():
        bat.unlink()
    return {"ok": True}

def check_update():
    try:
        import urllib.request
        with urllib.request.urlopen(f"{GITHUB_RAW}/VERSION", timeout=5) as r:
            latest = r.read().decode().strip()
        current = get_version()
        return {"current": current, "latest": latest, "update": latest != current}
    except:
        return {"current": get_version(), "latest": None, "update": False}

def get_modelo_status():
    """Verifica quantos combates novos há desde o último modelo subido."""
    import json as _j
    total_combates = 0
    for profile_dir in PROFILES_DIR.iterdir():
        if not profile_dir.is_dir(): continue
        f = profile_dir / "combates_srv.json"
        if f.exists():
            try: total_combates += len(_j.loads(f.read_text(encoding="utf-8")))
            except: pass
    modelo_path = BASE_DIR / "modelo_combate.json"
    modelo_combates = 0
    if modelo_path.exists():
        try:
            m = _j.loads(modelo_path.read_text(encoding="utf-8"))
            modelo_combates = m.get("total_combates", 0)
        except: pass
    novos = total_combates - modelo_combates
    return {
        "total_combates": total_combates,
        "modelo_combates": modelo_combates,
        "combates_novos": novos,
        "tem_modelo": modelo_path.exists(),
        "badge": novos >= 20,  # mostra badge se 20+ combates novos
    }

def export_modelo():
    """Consolida combates de todos os perfis e gera modelo_combate.json."""
    import json as _j, sys
    todos_combates = []
    for profile_dir in PROFILES_DIR.iterdir():
        if not profile_dir.is_dir(): continue
        f = profile_dir / "combates_srv.json"
        if f.exists():
            try: todos_combates.extend(_j.loads(f.read_text(encoding="utf-8")))
            except: pass
    if not todos_combates:
        return {"ok": False, "error": "Nenhum combate encontrado"}
    # Ordena por timestamp
    todos_combates.sort(key=lambda x: x.get("ts",""))
    total = len(todos_combates)
    vitorias = sum(1 for c in todos_combates if c["resultado"] == "vitoria")
    # WR por hit rate
    wr_hr = {}
    for c in todos_combates:
        eu_ac = c.get("eu_ac",0); adv_blq = c.get("adv_blq",0)
        if eu_ac > 0 and adv_blq > 0:
            taxa = round(eu_ac/(eu_ac+adv_blq)*10)/10
            k = f"{taxa:.1f}"
            wr_hr.setdefault(k, {"t":0,"v":0})
            wr_hr[k]["t"] += 1
            if c["resultado"] == "vitoria": wr_hr[k]["v"] += 1
    # WR por delta level
    wr_lv = {}
    for c in todos_combates:
        delta = str(max(-5, min(10, c.get("adv_lv",0)-c.get("eu_lv",0))))
        wr_lv.setdefault(delta, {"t":0,"v":0})
        wr_lv[delta]["t"] += 1
        if c["resultado"] == "vitoria": wr_lv[delta]["v"] += 1
    # Calibração score
    calib = {}
    for c in todos_combates:
        k = str(int(c.get("score_previsto",0)//10)*10)
        calib.setdefault(k, {"t":0,"v":0})
        calib[k]["t"] += 1
        if c["resultado"] == "vitoria": calib[k]["v"] += 1

    import datetime
    modelo = {
        "versao": datetime.datetime.now().strftime("%Y%m%d_%H%M"),
        "total_combates": total,
        "win_rate_global": round(vitorias/total*100,1),
        "wr_por_hit_rate": {k: round(v["v"]/v["t"]*100,1) for k,v in wr_hr.items() if v["t"]>=3},
        "wr_por_delta_level": {k: round(v["v"]/v["t"]*100,1) for k,v in wr_lv.items() if v["t"]>=3},
        "calibracao_score": {k: round(v["v"]/v["t"]*100,1) for k,v in calib.items() if v["t"]>=3},
        "gold_total": sum(c.get("gold",0) for c in todos_combates),
        "xp_total": sum(c.get("xp",0) for c in todos_combates),
    }
    out = BASE_DIR / "modelo_combate.json"
    out.write_text(_j.dumps(modelo, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "total_combates": total, "win_rate": modelo["win_rate_global"],
            "arquivo": str(out)}

def download_update():
    import urllib.request, shutil
    for name, p in list(running_bots.items()):
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except: p.kill()
    updated, errors = [], []
    for fname in ["bot.py", "dashboard.html", "launcher.html", "bot_bg.py", "dashboard_bg.html", "modelo_combate.json"]:
        try:
            bak = BASE_DIR / (fname + ".bak")
            src = BASE_DIR / fname
            if src.exists():
                shutil.copy(src, bak)
            with urllib.request.urlopen(f"{GITHUB_RAW}/{fname}", timeout=15) as r:
                (BASE_DIR / fname).write_bytes(r.read())
            updated.append(fname)
        except Exception as e:
            errors.append(f"{fname}: {e}")
    try:
        with urllib.request.urlopen(f"{GITHUB_RAW}/VERSION", timeout=5) as r:
            (BASE_DIR / "VERSION").write_bytes(r.read())
    except:
        pass
    try:
        with urllib.request.urlopen(f"{GITHUB_RAW}/VERSION_BG", timeout=5) as r:
            (BASE_DIR / "VERSION_BG").write_bytes(r.read())
    except:
        pass
    return {"ok": len(errors) == 0, "updated": updated, "errors": errors}

def capture_cookie_browser(server="int7"):
    try:
        from selenium import webdriver
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        return {"ok": False, "error": "selenium nao instalado. Rode: pip install selenium webdriver-manager"}

    driver = None
    errors = []

    for Browser, Manager, name in [
        ("chrome", "ChromeDriverManager", "Chrome"),
        ("edge",   "EdgeChromiumDriverManager", "Edge"),
        ("firefox","GeckoDriverManager", "Firefox"),
    ]:
        if driver: break
        try:
            if Browser == "chrome":
                from selenium.webdriver.chrome.options import Options
                from selenium.webdriver.chrome.service import Service
                from webdriver_manager.chrome import ChromeDriverManager
                opts = Options(); opts.add_argument("--start-maximized")
                driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
            elif Browser == "edge":
                from selenium.webdriver.edge.options import Options
                from selenium.webdriver.edge.service import Service
                from webdriver_manager.microsoft import EdgeChromiumDriverManager
                opts = Options(); opts.add_argument("--start-maximized")
                driver = webdriver.Edge(service=Service(EdgeChromiumDriverManager().install()), options=opts)
            else:
                from selenium.webdriver.firefox.options import Options
                from selenium.webdriver.firefox.service import Service
                from webdriver_manager.firefox import GeckoDriverManager
                driver = webdriver.Firefox(service=Service(GeckoDriverManager().install()))
            browser_name = name
        except Exception as e:
            errors.append(f"{name}: {e}")

    if not driver:
        return {"ok": False, "error": "Nenhum browser encontrado. " + " | ".join(errors)}

    try:
        driver.get(f"https://{server}.knightfight.moonid.net/status/")
        WebDriverWait(driver, 300).until(
            lambda d: "status" in d.current_url and d.find_elements("id", "character-main")
        )
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in driver.get_cookies())
        userid = ""
        try:
            el = driver.find_element("css selector", ".your_id")
            m  = re.search(r'(\d{6,})', el.text)
            if m: userid = m.group(1)
        except: pass
        driver.quit()
        return {"ok": True, "cookies": cookie_str, "userid": userid, "browser": browser_name}
    except Exception as e:
        try: driver.quit()
        except: pass
        return {"ok": False, "error": str(e)}

# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200); self._cors(); self.end_headers()

    def _json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self._cors(); self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not n: return {}
            data = self.rfile.read(n)
            return json.loads(data) if data.strip() else {}
        except:
            return {}

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/launcher"):
            self._file("launcher.html", "text/html")
        elif p == "/api/profiles":
            self._json(get_profiles())
        elif p == "/api/version":
            self._json(check_update())
        elif p.startswith("/api/log/"):
            self._json({"lines": get_log_tail(p.split("/")[-1], 30)})
        elif p == "/api/bg/diag":
            import json as _j
            diag = {}
            for d in PROFILES_DIR.iterdir():
                if d.is_dir():
                    cfg_p = d / "config.json"
                    bot_bg = BASE_DIR / "bot_bg.py"
                    diag[d.name] = {
                        "profile_dir": str(d),
                        "profile_exists": d.exists(),
                        "config_exists": cfg_p.exists(),
                        "bot_bg_exists": bot_bg.exists(),
                        "bot_bg_path": str(bot_bg),
                    }
            self._json(diag)
        elif p.startswith("/api/bg/stop/"):
            self._json(stop_bg_bot(p.split("/")[-1] or p.split("/")[-2]))
        elif p.startswith("/api/bg/status/"):
            self._json(status_bg_bot(p.split("/")[-1] or p.split("/")[-2]))
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        d = self._body()
        p = self.path
        if   p == "/api/start":          self._json(start_bot(d["name"]))
        elif p == "/api/stop":           self._json(stop_bot(d["name"]))
        elif p == "/api/save":           self._json(save_profile(d))
        elif p == "/api/delete":         self._json(delete_profile(d["name"]))
        elif p == "/api/update":         self._json(download_update())
        elif p.startswith("/api/bg/start/"):
            parts = [x for x in p.split("/") if x]
            name = parts[-1] if parts else ""
            body = self._body()
            modo = body.get("modo", "free") if body else "free"
            # Inicia em thread separada para não bloquear o servidor
            import threading as _t
            bg_key = f"BG_{name.upper()}"
            def _bg_thread():
                try:
                    r = start_bg_bot(name, modo)
                    print(f"[BG] start_bg_bot({name}) = {r}", flush=True)
                    print(f"[BG] running_bots keys: {list(running_bots.keys())}", flush=True)
                except Exception as e:
                    import traceback
                    print(f"[BG] ERRO: {e}", flush=True)
                    traceback.print_exc()
            _t.Thread(target=_bg_thread, daemon=True).start()
            # Calcula porta BG para retornar ao JS
            try:
                import json as _j2
                cfg_p = BASE_DIR / "profiles" / name.upper() / "config.json"
                _cfg = _j2.loads(cfg_p.read_text(encoding="utf-8")) if cfg_p.exists() else {}
                bg_port = _cfg.get("port", 8765) + 5
            except:
                bg_port = 8770
            self._json({"ok": True, "pid": -1, "port": bg_port, "msg": "Iniciando..."})
        elif p == "/api/capture-cookie":
            result = {}
            def run(): result["r"] = capture_cookie_browser(d.get("server", "int7"))
            t = threading.Thread(target=run, daemon=True)
            t.start(); t.join(timeout=320)
            self._json(result.get("r", {"ok": False, "error": "Timeout"}))
        else:
            self.send_response(404); self.end_headers()

    def _file(self, fname, ctype):
        # Procura na pasta atual e depois na BASE_DIR
        for path in [BASE_DIR / fname, Path(fname)]:
            if path.exists():
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self._cors(); self.end_headers()
                self.wfile.write(path.read_bytes())
                return
        self.send_response(404); self.end_headers()

# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    import sys, io
    if hasattr(sys.stdout, 'buffer') and sys.stdout.encoding.lower().replace('-','') != 'utf8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer') and sys.stderr.encoding.lower().replace('-','') != 'utf8':
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    server = ThreadingHTTPServer(("localhost", LAUNCHER_PORT), Handler)
    server.allow_reuse_address = True
    url    = f"http://localhost:{LAUNCHER_PORT}/launcher"
    print(f"KnightFight Bot Launcher {get_version()}")
    print(f"Abrindo {url}")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Launcher encerrado.")
        for p in running_bots.values():
            if p.poll() is None:
                p.terminate()

if __name__ == "__main__":
    run()
