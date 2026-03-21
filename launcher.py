"""
KnightFight Bot — Launcher
Servidor HTTP local que serve a interface de gerenciamento de perfis.
Roda na porta 8764, abre o browser automaticamente.
"""
import os, sys, json, subprocess, threading, time, re, signal
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

VERSION = "1.0.0"
LAUNCHER_PORT = 8764
GITHUB_RAW = "https://raw.githubusercontent.com/bohrerlbs/KnightFightBot/main"

# Pasta base = onde o launcher.py (ou .exe) está
BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
PROFILES_DIR = BASE_DIR / "profiles"
PROFILES_DIR.mkdir(exist_ok=True)

# Processos rodando { nome_perfil: subprocess }
running_bots = {}

# ── Helpers ──────────────────────────────────────────────────────────────────

def get_profiles():
    profiles = []
    if PROFILES_DIR.exists():
        for d in sorted(PROFILES_DIR.iterdir()):
            cfg_path = d / "config.json"
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    cfg["_name"] = d.name
                    cfg["_running"] = d.name in running_bots and running_bots[d.name].poll() is None
                    cfg["_log_tail"] = get_log_tail(d.name, 5)
                    profiles.append(cfg)
                except:
                    pass
    return profiles

def get_log_tail(profile_name, lines=20):
    log_path = PROFILES_DIR / profile_name / "bot.log"
    if not log_path.exists():
        return []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return [l.rstrip() for l in all_lines[-lines:]]
    except:
        return []

def start_bot(profile_name):
    if profile_name in running_bots and running_bots[profile_name].poll() is None:
        return {"ok": False, "error": "Já está rodando"}
    bot_py = BASE_DIR / "bot.py"
    if not bot_py.exists():
        return {"ok": False, "error": "bot.py não encontrado"}
    cmd = [sys.executable, str(bot_py), "--profile", profile_name]
    try:
        p = subprocess.Popen(cmd, cwd=str(BASE_DIR),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        running_bots[profile_name] = p
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def stop_bot(profile_name):
    p = running_bots.get(profile_name)
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
    name = re.sub(r'[^\w\-]', '_', data.get("name","novo")).lower()
    path = PROFILES_DIR / name
    path.mkdir(exist_ok=True)
    cfg = {
        "profile": name,
        "server":  data.get("server","int7"),
        "userid":  data.get("userid",""),
        "cookies": data.get("cookies",""),
        "port":    int(data.get("port", 8765 + len(get_profiles()))),
    }
    (path / "config.json").write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    # Cria .bat
    bat = f'@echo off\ncd /d "{BASE_DIR}"\npython bot.py --profile {name}\npause\n'
    (BASE_DIR / f"iniciar_{name}.bat").write_text(bat)
    return {"ok": True, "name": name, "config": cfg}

def delete_profile(name):
    import shutil
    path = PROFILES_DIR / name
    if path.exists():
        shutil.rmtree(path)
    bat = BASE_DIR / f"iniciar_{name}.bat"
    if bat.exists(): bat.unlink()
    return {"ok": True}

def check_update():
    try:
        import urllib.request
        url = f"{GITHUB_RAW}/VERSION"
        with urllib.request.urlopen(url, timeout=5) as r:
            latest = r.read().decode().strip()
        return {"current": VERSION, "latest": latest, "update": latest != VERSION}
    except:
        return {"current": VERSION, "latest": None, "update": False}


def download_update():
    """
    Baixa bot.py e dashboard.html atualizados do GitHub.
    Para todos os bots, atualiza os arquivos e avisa para reiniciar.
    """
    import urllib.request, shutil

    files_to_update = ["bot.py", "dashboard.html", "launcher.html"]
    updated = []
    errors = []

    # Para todos os bots rodando
    for name, p in list(running_bots.items()):
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=5)
            except: p.kill()

    for fname in files_to_update:
        url = f"{GITHUB_RAW}/{fname}"
        dest = BASE_DIR / fname
        try:
            # Backup do arquivo atual
            if dest.exists():
                shutil.copy(dest, str(dest) + ".bak")
            with urllib.request.urlopen(url, timeout=15) as r:
                content = r.read()
            dest.write_bytes(content)
            updated.append(fname)
        except Exception as e:
            errors.append(f"{fname}: {e}")

    # Atualiza VERSION local
    try:
        info = check_update()
        if info.get("latest"):
            (BASE_DIR / "VERSION").write_text(info["latest"])
    except:
        pass

    return {
        "ok": len(errors) == 0,
        "updated": updated,
        "errors": errors,
        "restart_required": True
    }

def capture_cookie_browser(server="int7"):
    """
    Abre o browser usando selenium, navega para o login do KnightFight,
    aguarda o usuário logar e captura os cookies automaticamente.
    Usa webdriver-manager para baixar o driver correto.
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError:
        return {"ok": False, "error": "selenium não instalado. Rode: pip install selenium webdriver-manager"}

    # Tenta Chrome → Edge → Firefox
    driver = None
    browser_name = ""
    errors = []

    # Chrome
    try:
        from selenium.webdriver.chrome.options import Options as COptions
        from selenium.webdriver.chrome.service import Service as CService
        from webdriver_manager.chrome import ChromeDriverManager
        opts = COptions()
        opts.add_argument("--start-maximized")
        driver = webdriver.Chrome(service=CService(ChromeDriverManager().install()), options=opts)
        browser_name = "Chrome"
    except Exception as e:
        errors.append(f"Chrome: {e}")

    # Edge
    if not driver:
        try:
            from selenium.webdriver.edge.options import Options as EOptions
            from selenium.webdriver.edge.service import Service as EService
            from webdriver_manager.microsoft import EdgeChromiumDriverManager
            opts = EOptions()
            opts.add_argument("--start-maximized")
            driver = webdriver.Edge(service=EService(EdgeChromiumDriverManager().install()), options=opts)
            browser_name = "Edge"
        except Exception as e:
            errors.append(f"Edge: {e}")

    # Firefox
    if not driver:
        try:
            from selenium.webdriver.firefox.options import Options as FOptions
            from selenium.webdriver.firefox.service import Service as FService
            from webdriver_manager.firefox import GeckoDriverManager
            opts = FOptions()
            driver = webdriver.Firefox(service=FService(GeckoDriverManager().install()), options=opts)
            browser_name = "Firefox"
        except Exception as e:
            errors.append(f"Firefox: {e}")

    if not driver:
        return {"ok": False, "error": "Nenhum browser encontrado. " + " | ".join(errors)}

    try:
        url = f"https://{server}.knightfight.moonid.net/status/"
        driver.get(url)

        # Aguarda até a página de status carregar (usuário faz login)
        wait = WebDriverWait(driver, 300)  # até 5 minutos para logar
        wait.until(lambda d: "status" in d.current_url and "login" not in d.current_url.lower()
                   and d.find_elements("id", "character-main"))

        # Extrai cookies
        cookies = driver.get_cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        # Extrai UserID da página
        userid = ""
        try:
            el = driver.find_element("css selector", ".your_id")
            m = re.search(r'UserID:\s*(\d+)', el.text)
            if m: userid = m.group(1)
        except:
            pass

        driver.quit()
        return {"ok": True, "cookies": cookie_str, "userid": userid, "browser": browser_name}
    except Exception as e:
        try: driver.quit()
        except: pass
        return {"ok": False, "error": str(e)}

# ── HTTP Handler ──────────────────────────────────────────────────────────────

class LauncherHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/launcher"):
            self._serve_html()
        elif path == "/api/profiles":
            self._json(get_profiles())
        elif path.startswith("/api/log/"):
            name = path.split("/")[-1]
            self._json({"lines": get_log_tail(name, 30)})
        elif path == "/api/version":
            self._json(check_update())
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        path = self.path
        data = self._read_body()

        if path == "/api/start":
            self._json(start_bot(data["name"]))
        elif path == "/api/stop":
            self._json(stop_bot(data["name"]))
        elif path == "/api/save":
            self._json(save_profile(data))
        elif path == "/api/delete":
            self._json(delete_profile(data["name"]))
        elif path == "/api/update":
            self._json(download_update())
        elif path == "/api/capture-cookie":
            # Roda em thread para não bloquear o servidor
            result_holder = {}
            def run():
                result_holder["r"] = capture_cookie_browser(data.get("server","int7"))
            t = threading.Thread(target=run, daemon=True)
            t.start()
            t.join(timeout=320)
            self._json(result_holder.get("r", {"ok": False, "error": "Timeout"}))
        else:
            self.send_response(404); self.end_headers()

    def _serve_html(self):
        html_path = BASE_DIR / "launcher.html"
        if html_path.exists():
            body = html_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    server = HTTPServer(("localhost", LAUNCHER_PORT), LauncherHandler)
    url = f"http://localhost:{LAUNCHER_PORT}/launcher"
    print(f"⚔  KnightFight Bot Launcher")
    print(f"   Abrindo {url}")

    # Abre browser após 1s (dá tempo do servidor subir)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLauncher encerrado.")
        # Para todos os bots ao fechar
        for name, p in running_bots.items():
            if p.poll() is None:
                p.terminate()

if __name__ == "__main__":
    run()
