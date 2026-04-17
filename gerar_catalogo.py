#!/usr/bin/env python3
"""Gera catálogos JSON estáticos a partir dos arquivos TXT da pasta itens/."""
import csv, json, os, re

BASE = os.path.join(os.path.dirname(__file__), "itens")
OUT  = os.path.join(os.path.dirname(__file__), "catalogo")
os.makedirs(OUT, exist_ok=True)

def parse_num(s, default=0):
    s = str(s).strip()
    if not s:
        return default
    try:
        return float(s.replace(",", "."))
    except Exception:
        return default

def parse_int(s, default=0):
    return int(parse_num(s, default))

def parse_alignment(s):
    s = str(s).strip()
    if not s:
        return 0
    try:
        return int(s.replace("+", ""))
    except Exception:
        return 0

def fix_url(nome):
    """Remove sufixo [Bazar] e retorna (nome_limpo, is_bazar)."""
    bazar = "[Bazar]" in nome or " - Bazar" in nome
    nome_clean = nome.replace(" [Bazar]", "").replace(" - Bazar", "").strip()
    return nome_clean, bazar

def total_stats_anel(r):
    return (r.get("forca", 0) + r.get("agilidade", 0) + r.get("resistencia", 0)
            + r.get("arte_combate", 0) + r.get("bloqueio", 0))

# ── ANEIS ──────────────────────────────────────────────────────────────────
aneis = []
with open(os.path.join(BASE, "aneis.txt"), encoding="utf-8") as f:
    reader = csv.DictReader(f, skipinitialspace=True)
    for row in reader:
        nome_raw = row.get("Nome", "").strip()
        if not nome_raw or "????" in nome_raw:
            continue
        nome, bazar = fix_url(nome_raw)
        cond = row.get("Condição", "").strip()
        req_level = 0
        m = re.search(r"[Ll]evel[:\s]*(\d+)", cond)
        if m:
            req_level = int(m.group(1))
        aneis.append({
            "nome":         nome,
            "req_level":    req_level,
            "forca":        parse_int(row.get("Força", 0)),
            "agilidade":    parse_int(row.get("Agilidade", 0)),
            "resistencia":  parse_int(row.get("Resistência", 0)),
            "arte_combate": parse_int(row.get("Arte de combate", 0)),
            "bloqueio":     parse_int(row.get("Bloqueio", 0)),
            "preco_venda":  parse_int(row.get("Preço de Venda", 0)),
            "req_alignment": parse_alignment(row.get("Moral", "")),
            "bazar":        bazar,
        })
# Ordena por req_level ASC (menor primeiro)
aneis.sort(key=lambda x: x["req_level"])
with open(os.path.join(OUT, "aneis.json"), "w", encoding="utf-8") as f:
    json.dump(aneis, f, ensure_ascii=False, indent=2)
print(f"aneis.json: {len(aneis)} itens")

# ── AMULETOS ───────────────────────────────────────────────────────────────
amuletos = []
with open(os.path.join(BASE, "amuletos.txt"), encoding="utf-8") as f:
    reader = csv.DictReader(f, skipinitialspace=True)
    for row in reader:
        nome_raw = row.get("Nome", "").strip()
        if not nome_raw or "????" in nome_raw:
            continue
        nome, bazar = fix_url(nome_raw)
        req_level = parse_int(row.get("Level", 0))
        raio_raw  = str(row.get("Raio-Danos", "")).strip()
        raio_valor, raio_tipo = 0, None
        if raio_raw:
            m = re.match(r"(-?\d+)(?:\s*\(([^)]+)\))?", raio_raw)
            if m:
                raio_valor = int(m.group(1))
                raio_tipo  = m.group(2)  # "Fogo", "Gelo", etc.
        amuletos.append({
            "nome":          nome,
            "req_level":     req_level,
            "raio_dano":     raio_valor,
            "raio_tipo":     raio_tipo,
            "forca":         parse_int(row.get("Força", 0)),
            "agilidade":     parse_int(row.get("Agilidade", 0)),
            "resistencia":   parse_int(row.get("Resistência", 0)),
            "arte_combate":  parse_int(row.get("Arte de combate", 0)),
            "bloqueio":      parse_int(row.get("Bloqueio", 0)),
            "preco_venda":   parse_int(row.get("Preço de Venda", 0)),
            "req_alignment": parse_alignment(row.get("Moral", "")),
            "bazar":         bazar,
        })
amuletos.sort(key=lambda x: x["req_level"])
with open(os.path.join(OUT, "amuletos.json"), "w", encoding="utf-8") as f:
    json.dump(amuletos, f, ensure_ascii=False, indent=2)
print(f"amuletos.json: {len(amuletos)} itens")

# ── ARMAS 1H ───────────────────────────────────────────────────────────────
armas_1h = []
with open(os.path.join(BASE, "1h.txt"), encoding="utf-8") as f:
    reader = csv.DictReader(f, skipinitialspace=True)
    for row in reader:
        nome_raw = row.get("Nome", "").strip()
        if not nome_raw or "????" in nome_raw:
            continue
        nome, bazar = fix_url(nome_raw)
        req_skill = parse_int(row.get("Skills Uma Mão", 0))
        armas_1h.append({
            "nome":          nome,
            "tipo":          "einhand",
            "req_skill":     req_skill,
            "dano_min":      parse_int(row.get("Danos Min", 0)),
            "dano_max":      parse_int(row.get("Danos Max", 0)),
            "encaixes":      parse_int(row.get("Encaixe", 0)),
            "resistencia":   parse_int(row.get("Resistência", 0)),
            "agilidade":     parse_int(row.get("Agilidade", 0)),
            "preco_venda":   parse_int(row.get("Preço de Venda", 0)),
            "req_alignment": parse_alignment(row.get("Moral", "")),
            "bazar":         bazar,
        })
armas_1h.sort(key=lambda x: x["req_skill"])
with open(os.path.join(OUT, "armas_1h.json"), "w", encoding="utf-8") as f:
    json.dump(armas_1h, f, ensure_ascii=False, indent=2)
print(f"armas_1h.json: {len(armas_1h)} itens")

# ── ARMAS 2H ───────────────────────────────────────────────────────────────
armas_2h = []
with open(os.path.join(BASE, "2h.txt"), encoding="utf-8") as f:
    reader = csv.DictReader(f, skipinitialspace=True)
    for row in reader:
        nome_raw = row.get("Nome", "").strip()
        if not nome_raw or "????" in nome_raw:
            continue
        nome, bazar = fix_url(nome_raw)
        req_skill = parse_int(row.get("Skills Duas Mãos", 0))
        armas_2h.append({
            "nome":          nome,
            "tipo":          "zweihand",
            "req_skill":     req_skill,
            "dano_min":      parse_int(row.get("Danos Min", 0)),
            "dano_max":      parse_int(row.get("Danos Max", 0)),
            "encaixes":      parse_int(row.get("Encaixe", 0)),
            "resistencia":   parse_int(row.get("Resistência", 0)),
            "agilidade":     parse_int(row.get("Agilidade", 0)),
            "preco_venda":   parse_int(row.get("Preço de Venda", 0)),
            "req_alignment": parse_alignment(row.get("Moral", "")),
            "bazar":         bazar,
        })
armas_2h.sort(key=lambda x: x["req_skill"])
with open(os.path.join(OUT, "armas_2h.json"), "w", encoding="utf-8") as f:
    json.dump(armas_2h, f, ensure_ascii=False, indent=2)
print(f"armas_2h.json: {len(armas_2h)} itens")

# ── ARMADURAS ──────────────────────────────────────────────────────────────
armaduras = []
with open(os.path.join(BASE, "armadura.txt"), encoding="utf-8") as f:
    reader = csv.DictReader(f, skipinitialspace=True)
    for row in reader:
        nome_raw = row.get("Nome", "").strip()
        if not nome_raw or "????" in nome_raw:
            continue
        nome, bazar = fix_url(nome_raw)
        notas     = str(row.get("Notas", "")).strip()
        cond_val  = parse_int(row.get("Condição Armadura", 0))
        # "Condição - Level" = o campo é nível, não skill de armadura
        req_skill = 0
        req_level = 0
        if "Condição - Level" in notas or "Condição – Level" in notas:
            req_level = cond_val
        else:
            req_skill = cond_val
        armaduras.append({
            "nome":          nome,
            "req_skill":     req_skill,
            "req_level":     req_level,
            "defesa_min":    parse_num(row.get("Defesa Min", 0)),
            "defesa_max":    parse_num(row.get("Defesa Max", 0)),
            "resistencia":   parse_int(row.get("Resistência", 0)),
            "preco_venda":   parse_int(row.get("Preço de Venda", 0)),
            "req_alignment": parse_alignment(row.get("Moral", "")),
            "bazar":         bazar,
        })
armaduras.sort(key=lambda x: x["req_skill"])
with open(os.path.join(OUT, "armaduras.json"), "w", encoding="utf-8") as f:
    json.dump(armaduras, f, ensure_ascii=False, indent=2)
print(f"armaduras.json: {len(armaduras)} itens")

# ── ESCUDOS ────────────────────────────────────────────────────────────────
escudos = []
with open(os.path.join(BASE, "escudos.txt"), encoding="utf-8") as f:
    reader = csv.DictReader(f, skipinitialspace=True)
    for row in reader:
        nome_raw = row.get("Nome", "").strip()
        if not nome_raw or "????" in nome_raw:
            continue
        nome, bazar = fix_url(nome_raw)
        notas    = str(row.get("Notas", "")).strip()
        req_skill = parse_int(row.get("Condição Armadura", 0))
        # "Skills de duas mãos" em notas → req_skill é de 2h, não armadura
        tipo_req = "ruestung"
        if "duas mãos" in notas.lower() or "zweihand" in notas.lower():
            tipo_req = "zweihand"
        escudos.append({
            "nome":          nome,
            "req_skill":     req_skill,
            "tipo_req":      tipo_req,
            "defesa_min":    parse_num(row.get("Defesa Min", 0)),
            "defesa_max":    parse_num(row.get("Defesa Max", 0)),
            "agilidade":     parse_int(row.get("Agilidade", 0)),
            "resistencia":   parse_int(row.get("Resistência", 0)),
            "bloqueio":      parse_int(row.get("Bloqueio", 0)),
            "preco_venda":   parse_int(row.get("Preço de Venda", 0)),
            "req_alignment": parse_alignment(row.get("Moral", "")),
            "bazar":         bazar,
        })
escudos.sort(key=lambda x: x["req_skill"])
with open(os.path.join(OUT, "escudos.json"), "w", encoding="utf-8") as f:
    json.dump(escudos, f, ensure_ascii=False, indent=2)
print(f"escudos.json: {len(escudos)} itens")

# ── PEDRAS (ENCAIXES) ───────────────────────────────────────────────────────
pedras = []
with open(os.path.join(BASE, "encaixes.txt"), encoding="utf-8") as f:
    reader = csv.DictReader(f, skipinitialspace=True)
    for row in reader:
        nome_raw = row.get("Nome", "").strip()
        if not nome_raw or "????" in nome_raw:
            continue
        notas = str(row.get("Notas", "")).strip()
        bazar = "Bazar" in notas or "[Bazar]" in nome_raw or " - Bazar" in nome_raw
        nome  = nome_raw.replace(" - Bazar", "").replace(" [Bazar]", "").strip()
        req_level = parse_int(row.get("Condição Level", 0))
        pedras.append({
            "nome":          nome,
            "req_level":     req_level,
            "tipo":          str(row.get("Dano Tipo", "")).strip(),
            "valor":         parse_int(row.get("Dano Valor", 0)),
            "preco_venda":   parse_int(row.get("Preço de Venda", 0)),
            "req_alignment": parse_alignment(row.get("Moral", "")),
            "bazar":         bazar,
        })
pedras.sort(key=lambda x: (x["tipo"], x["valor"]))
with open(os.path.join(OUT, "pedras.json"), "w", encoding="utf-8") as f:
    json.dump(pedras, f, ensure_ascii=False, indent=2)
print(f"pedras.json: {len(pedras)} itens")

print("\nCatálogos gerados em:", OUT)
