#!/usr/bin/env python3
# combined.py
import os
import requests
import threading
import time
import logging
import random
import urllib.parse
import json
import re
from datetime import datetime, timedelta
from flask import Flask, jsonify, request

# Discord imports
import discord
from discord.ext import commands, tasks

# -------------------------
# LOG
# -------------------------
logging.basicConfig(level=logging.INFO, format='[MINI] %(message)s')

# -------------------------
# FLASK
# -------------------------
app = Flask(__name__)

# ==============================
# CONFIG - JOBIDS (mini API)
# ==============================
GAME_ID = os.environ.get("GAME_ID", "109983668079237")
BASE_URL = f"https://games.roblox.com/v1/games/{GAME_ID}/servers/Public?sortOrder=Asc&limit=100"
MAIN_API_URL = os.environ.get("MAIN_API_URL", "https://main-jobid-production.up.railway.app/add-pool")

SEND_INTERVAL = int(os.environ.get("SEND_INTERVAL", "30"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))
SEND_MIN_SERVERS = int(os.environ.get("SEND_MIN_SERVERS", "1"))
MAX_PAGES_PER_CYCLE = int(os.environ.get("MAX_PAGES_PER_CYCLE", "10"))

MIN_PLAYERS = int(os.environ.get("MIN_PLAYERS", "0"))
MAX_PLAYERS = int(os.environ.get("MAX_PLAYERS", "999"))

POOL_FILE = os.environ.get("POOL_FILE", "pool.json")

# Global list with last filtered job ids (exposto em /jobs)
LAST_JOBIDS = []

# ==============================
# PROXIES
# ==============================
def normalize_proxy(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    parts = raw.split(":")
    if len(parts) >= 4:  # host:port:user:pass[:...]
        host = parts[0]
        port = parts[1]
        user = parts[2]
        pwd = ":".join(parts[3:])
        user_enc = urllib.parse.quote(user, safe="")
        pwd_enc = urllib.parse.quote(pwd, safe="")
        return f"http://{user_enc}:{pwd_enc}@{host}:{port}"
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    return raw

raw_proxies = os.environ.get("PROXIES", "")
PROXIES = [normalize_proxy(p) for p in raw_proxies.split(",") if p.strip()]

if not PROXIES:
    logging.warning("[WARN] Nenhuma proxy configurada — requisições diretas.")
else:
    logging.info(f"[INIT] {len(PROXIES)} proxies carregadas.")

# ==============================
# SALVAR LOCAL
# ==============================
def save_pool(job_ids):
    try:
        with open(POOL_FILE, "w", encoding="utf-8") as f:
            json.dump({"servers": job_ids}, f, indent=4)
        logging.info(f"[LOCAL] {POOL_FILE} atualizado com {len(job_ids)} servers.")
    except Exception as e:
        logging.error(f"[LOCAL ERRO] Falha ao salvar {POOL_FILE}: {e}")

# ==============================
# FETCH SERVERS
# ==============================
def fetch_all_roblox_servers(retries=3):
    all_servers = []
    cursor = None
    page_count = 0
    proxy_index = 0

    while True:
        proxy = random.choice(PROXIES) if PROXIES else None
        proxies = {"http": proxy, "https": proxy} if proxy else None

        try:
            url = BASE_URL + (f"&cursor={cursor}" if cursor else "")
            page_count += 1
            logging.info(f"[FETCH] Página {page_count} via {proxy or 'sem proxy'}...")

            r = requests.get(url, proxies=proxies, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                logging.warning("[429] Too Many Requests — trocando de proxy...")
                time.sleep(1)
                continue

            r.raise_for_status()
            data = r.json()
            servers = data.get("data", [])
            all_servers.extend(servers)
            cursor = data.get("nextPageCursor")

            logging.info(f"[PAGE {page_count}] +{len(servers)} servers (Total: {len(all_servers)})")

            if not cursor or page_count >= MAX_PAGES_PER_CYCLE:
                logging.info(f"[INFO] Limite de páginas atingido ({page_count}/{MAX_PAGES_PER_CYCLE}).")
                break

            time.sleep(0.5)

        except requests.exceptions.RequestException as e:
            logging.warning(f"[ERRO] Proxy {proxy or 'sem proxy'} falhou: {e}")
            time.sleep(1)
            proxy_index += 1
            if proxy_index >= (len(PROXIES) or 1) * retries:
                break

    return all_servers

# ==============================
# LOOP PRINCIPAL (jobids)
# ==============================
def fetch_and_send_loop():
    global LAST_JOBIDS

    while True:
        servers = fetch_all_roblox_servers()
        total_servers = len(servers)

        if not servers:
            logging.warning("⚠️ Nenhum servidor encontrado.")
            time.sleep(SEND_INTERVAL)
            continue

        job_ids = [
            s["id"]
            for s in servers
            if "id" in s and MIN_PLAYERS <= s.get("playing", 0) <= MAX_PLAYERS
        ]

        logging.info(f"[FILTER] {len(job_ids)} servers após filtro ({MIN_PLAYERS}–{MAX_PLAYERS} players)")

        LAST_JOBIDS = job_ids

        save_pool(job_ids)

        if len(job_ids) < SEND_MIN_SERVERS:
            logging.info(f"[SKIP] Apenas {len(job_ids)} válidos (mínimo: {SEND_MIN_SERVERS}).")
            time.sleep(SEND_INTERVAL)
            continue

        payload = {"servers": job_ids}
        try:
            resp = requests.post(MAIN_API_URL, json=payload, timeout=REQUEST_TIMEOUT)
            if resp.ok:
                added = resp.json().get("added", None)
                logging.info(f"✅ Enviados {len(job_ids)} — adicionados: {added}")
            else:
                logging.warning(f"⚠️ MAIN retornou {resp.status_code}: {resp.text}")
        except Exception as e:
            logging.exception(f"❌ Erro ao enviar para MAIN: {e}")

        time.sleep(SEND_INTERVAL)

# Inicia thread do loop de jobids
threading.Thread(target=fetch_and_send_loop, daemon=True).start()

# ==============================
# CONFIG - WEBHOOKS & BOT
# ==============================
# (Você pode substituir as variáveis abaixo por ENV vars)
WEBHOOK_A1 = os.environ.get("WEBHOOK_A1", "https://discord.com/api/webhooks/1434974603599675546/")
WEBHOOK_A2 = os.environ.get("WEBHOOK_A2", "https://discord.com/api/webhooks/1434974826539520090/")
WEBHOOK_B  = os.environ.get("WEBHOOK_B", "https://discord.com/api/webhooks/1434974948476190730/")
WEBHOOK_C  = os.environ.get("WEBHOOK_C", "https://discord.com/api/webhooks/1434975012837920768/")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")  # <-- coloque seu token aqui ou via ENV
STATS_CHANNEL_ID = int(os.environ.get("STATS_CHANNEL_ID", "1434686237184233523"))

PLACE_ID = int(os.environ.get("PLACE_ID", str(GAME_ID)))
CACHE_FILE = os.environ.get("CACHE_FILE", "cache.json")

SEND_INTERVAL_WEBHOOK = int(os.environ.get("SEND_INTERVAL_WEBHOOK", "30"))
RESET_INTERVAL = int(os.environ.get("RESET_INTERVAL", str(24*60*60)))  # default 24h

# Estado do bot/webhook
name_counter = {}
job_history = []  # histórico de secrets (com secrets e timestamps)
last_reset = datetime.now()
_state = {"use_first_webhook": True, "stats_message_id": None}

MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "50"))

# ==============================
# HELPERS WEBHOOK/BOT
# ==============================
def parse_generation(gen_str: str) -> float:
    """Converte strings como $1.5M/s em número absoluto."""
    if not gen_str:
        return 0.0
    s = str(gen_str).upper().replace("$", "").replace("/S", "").strip()
    m = re.search(r"([\d\.]+)\s*([KMB]?)", s)
    if not m:
        nums = re.findall(r"[\d\.]+", s)
        return float(nums[0]) if nums else 0.0
    val, suf = m.groups()
    try:
        v = float(val)
    except:
        return 0.0
    if suf == "K":
        v *= 1_000
    elif suf == "M":
        v *= 1_000_000
    elif suf == "B":
        v *= 1_000_000_000
    return v

def make_joiner_url(place_id: int, job_id: str) -> str:
    return f"https://chillihub1.github.io/chillihub-joiner/?placeId={place_id}&gameInstanceId={job_id}"

def make_teleport_script(place_id: int, job_id: str) -> str:
    return (
        f"local TeleportService = game:GetService('TeleportService')\n"
        f"local Players = game:GetService('Players')\n"
        f"TeleportService:TeleportToPlaceInstance({place_id}, '{job_id}', Players.LocalPlayer)"
    )

def build_embed_payload(name, generation, rarity, job_id):
    join_url = make_joiner_url(PLACE_ID, job_id)
    teleport_script = make_teleport_script(PLACE_ID, job_id)
    embed = {
        "title": "Charizard Notifier",
        "color": 16753920,
        "fields": [
            {"name": "Name", "value": f"```{name or 'Unknown'}```", "inline": True},
            {"name": "Generation", "value": f"```{generation or '0'}```", "inline": True},
            {"name": "Rarity", "value": f"```{rarity or 'Unknown'}```", "inline": True},
            {"name": "JOB ID", "value": f"```{job_id}```", "inline": False},
            {"name": "Join Link", "value": f"[**Entrar**]({join_url})", "inline": False},
            {"name": "Teleport Script", "value": f"```lua\n{teleport_script}\n```", "inline": False},
        ],
        "footer": {"text": f"Detectado em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}
    }
    components = [
        {"type": 1, "components":[{"type": 2,"style":5,"label":"Entrar","url":join_url}]}
    ]
    return {"embeds":[embed], "components":components}

def send_to_webhook(name, generation, rarity, job_id):
    gen_value = parse_generation(generation)
    webhook_url = None

    global _state
    if 1_000_000 < gen_value <= 10_000_000:
        webhook_url = WEBHOOK_A1 if _state.get("use_first_webhook", True) else WEBHOOK_A2
        _state["use_first_webhook"] = not _state.get("use_first_webhook", True)
        save_state()
    elif 10_000_000 < gen_value <= 100_000_000:
        webhook_url = WEBHOOK_B
    elif gen_value > 100_000_000:
        webhook_url = WEBHOOK_C
    else:
        # menor que 1M, não envia
        return

    payload = build_embed_payload(name, generation, rarity, job_id)
    try:
        r = requests.post(webhook_url, json=payload, timeout=8)
        if not r.ok:
            logging.warning(f"[ERRO WEBHOOK] {r.status_code} {r.text}")
        else:
            logging.info(f"[OK] enviado webhook para {name} (gen {generation})")
    except Exception as e:
        logging.exception("[ERRO request.post]")

# ==============================
# CACHE (load/save)
# ==============================
def load_cache():
    global name_counter, last_reset, _state, job_history
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                name_counter.update(data.get("names", {}))
                job_history.extend(data.get("job_history", []))
                if "last_reset" in data:
                    try:
                        last_reset = datetime.fromisoformat(data["last_reset"])
                    except:
                        pass
                if "use_first_webhook" in data:
                    _state["use_first_webhook"] = data["use_first_webhook"]
                if "stats_message_id" in data:
                    _state["stats_message_id"] = data["stats_message_id"]
            logging.info(f"[CACHE] carregado {len(name_counter)} nomes, {len(job_history)} jobs")
        except Exception as e:
            logging.warning("[WARN] falha ao carregar cache: %s", e)

def save_state():
    try:
        to_save = {
            "names": name_counter,
            "job_history": job_history,
            "last_reset": last_reset.isoformat(),
            "use_first_webhook": _state.get("use_first_webhook", True),
            "stats_message_id": _state.get("stats_message_id")
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
    except Exception as e:
        logging.warning("[WARN] falha ao salvar cache: %s", e)

def reset_cache():
    global name_counter, last_reset, job_history
    name_counter.clear()
    job_history.clear()
    last_reset = datetime.now()
    save_state()
    logging.info("[CACHE] resetado")

# ==============================
# BOT DISCORD
# ==============================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def build_stats_embed():
    """Cria embed com estatísticas dos secrets encontrados."""
    if not name_counter:
        embed = discord.Embed(
            title="📊 Estatísticas de Secrets",
            description="Nenhum secret encontrado ainda neste período.",
            color=discord.Color.blue()
        )
        return embed
    
    sorted_secrets = sorted(name_counter.items(), key=lambda x: x[1], reverse=True)
    total = sum(name_counter.values())
    
    embed = discord.Embed(
        title="📊 Estatísticas de Secrets - Últimas 24h",
        description=f"**Total de secrets encontrados:** `{total}`\n**Jobs únicos rastreados:** `{len(job_history)}`",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )
    
    top_10 = sorted_secrets[:10]
    if top_10:
        field_value = "\n".join([f"`{i+1}.` **{name}** - `{count}x`" for i, (name, count) in enumerate(top_10)])
        embed.add_field(name="🏆 Top 10 Secrets Mais Encontrados", value=field_value, inline=False)
    
    if job_history:
        recent_jobs = job_history[:5]
        jobs_text = []
        for job in recent_jobs:
            # job['timestamp'] expected format '%Y-%m-%d %H:%M:%S'
            try:
                ts = int(datetime.strptime(job['timestamp'], '%Y-%m-%d %H:%M:%S').timestamp())
                jobs_text.append(f"**{job['name']}** `{job['generation']}` - <t:{ts}:R>")
            except Exception:
                jobs_text.append(f"**{job['name']}** `{job['generation']}` - {job.get('timestamp')}")
        embed.add_field(name="🕐 Últimos 5 Encontrados", value="\n".join(jobs_text), inline=False)
    
    embed.add_field(name="📈 Tipos Únicos", value=f"`{len(name_counter)}`", inline=True)
    embed.add_field(name="🔄 Próximo Reset", value=f"<t:{int((last_reset + timedelta(seconds=RESET_INTERVAL)).timestamp())}:R>", inline=True)
    embed.set_footer(text="Acesse /jobs para ver job ids coletados • Atualizado")
    return embed

@bot.event
async def on_ready():
    logging.info(f"[BOT] Conectado como {bot.user}")
    # Tenta enviar/editar a mensagem inicial de stats no canal (se existir)
    try:
        channel = bot.get_channel(STATS_CHANNEL_ID)
        if channel:
            embed = build_stats_embed()
            try:
                msg = await channel.send(embed=embed)
                _state["stats_message_id"] = msg.id
                save_state()
                logging.info(f"[BOT] Mensagem de stats criada (ID: {msg.id})")
            except Exception as e:
                logging.warning(f"[BOT] Não foi possível enviar mensagem inicial de stats: {e}")
        else:
            logging.warning(f"[BOT] Canal {STATS_CHANNEL_ID} não encontrado.")
    except Exception as e:
        logging.warning(f"[BOT] Erro ao checar canal: {e}")

    # inicia task loop se não rodando
    if not send_stats.is_running():
        send_stats.start()

@bot.command(name="stats")
async def manual_stats(ctx):
    embed = build_stats_embed()
    await ctx.send(embed=embed)

@bot.command(name="reset")
@commands.has_permissions(administrator=True)
async def manual_reset(ctx):
    reset_cache()
    await ctx.send("✅ Estatísticas resetadas com sucesso!")

@tasks.loop(minutes=5)
async def send_stats():
    """Atualiza estatísticas a cada 5 minutos."""
    try:
        channel = bot.get_channel(STATS_CHANNEL_ID)
        if not channel:
            logging.warning(f"[ERRO BOT] Canal {STATS_CHANNEL_ID} não encontrado!")
            return
        
        embed = build_stats_embed()
        message_id = _state.get("stats_message_id")
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed)
                logging.info("[BOT] ✓ Estatísticas atualizadas (editadas)")
                return
            except discord.NotFound:
                logging.info("[BOT] Mensagem antiga não encontrada, criando nova...")
            except Exception as e:
                logging.warning(f"[ERRO BOT] Falha ao editar: {e}")
        
        msg = await channel.send(embed=embed)
        _state["stats_message_id"] = msg.id
        save_state()
        logging.info(f"[BOT] ✓ Nova mensagem de estatísticas criada (ID: {msg.id})")
    except Exception as e:
        logging.exception("[ERRO BOT] Falha ao enviar stats")

@send_stats.before_loop
async def before_send_stats():
    await bot.wait_until_ready()
    logging.info("[BOT] Loop de estatísticas pronto para iniciar")

# ==============================
# ENDPOINTS FLASK
# ==============================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "combined API running",
        "proxy_count": len(PROXIES),
        "game_id": GAME_ID,
        "target_api": MAIN_API_URL,
        "send_min_servers": SEND_MIN_SERVERS,
        "max_pages_per_cycle": MAX_PAGES_PER_CYCLE,
        "min_players": MIN_PLAYERS,
        "max_players": MAX_PLAYERS
    })

@app.route("/jobids", methods=["GET"])
def jobids():
    return jsonify({
        "count": len(LAST_JOBIDS),
        "servers": LAST_JOBIDS
    })

# -------------------------
# MODIFIED: /jobs agora expõe os job ids coletados (LAST_JOBIDS)
# -------------------------
@app.route("/jobs", methods=["GET"])
def jobs():
    global LAST_JOBIDS

    # Se nada foi coletado ainda, tenta ler o pool.json
    if not LAST_JOBIDS and os.path.exists(POOL_FILE):
        try:
            with open(POOL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                LAST_JOBIDS = data.get("servers", [])
        except:
            pass

    return jsonify({
        "count": len(LAST_JOBIDS),
        "servers": LAST_JOBIDS
    })

# Para compatibilidade: manter endpoint que retorna job_history em /jobs_history
@app.route("/jobs_history", methods=["GET"])
def jobs_history():
    """Retorna histórico de secrets detectados (job_history)."""
    return jsonify(job_history)

# Endpoint que o webhook original usava para receber secrets
@app.route("/api", methods=["POST"])
def receive_api():
    try:
        data = request.json or {}
        name = data.get("Name") or data.get("name")
        generation = data.get("Generation") or data.get("generation")
        job_id = data.get("JobId") or data.get("jobId") or data.get("job_id")
        rarity = data.get("Rarity") or data.get("rarity") or "Unknown"

        if not all([name, generation, job_id]):
            return jsonify({"error":"Campos faltando"}),400

        # envia ao webhook apropriado
        send_to_webhook(name, generation, rarity, job_id)

        # contar
        name_counter[name] = name_counter.get(name,0)+1
        
        # Adicionar ao histórico de jobs
        job_entry = {
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "name": name,
            "generation": generation,
            "rarity": rarity,
            "placeId": str(PLACE_ID),
            "jobId": job_id
        }
        
        # Adiciona no início da lista
        job_history.insert(0, job_entry)
        
        # Mantém apenas os últimos MAX_HISTORY
        if len(job_history) > MAX_HISTORY:
            job_history.pop()
        
        save_state()
        return jsonify({"status":"OK"}),200
    except Exception as e:
        logging.exception("[ERRO API]")
        return jsonify({"error":str(e)}),500

# ==============================
# RESET LOOP (24h ou RESET_INTERVAL)
# ==============================
def reset_loop():
    global last_reset
    while True:
        time.sleep(60)
        try:
            if (datetime.now() - last_reset).total_seconds() >= RESET_INTERVAL:
                reset_cache()
                last_reset = datetime.now()
        except Exception as e:
            logging.warning("[RESET LOOP] %s", e)

# ==============================
# RUN FLASK em thread
# ==============================
def run_flask():
    port = int(os.environ.get("PORT", "8080"))
    logging.info(f"[FLASK] rodando na porta {port}")
    # debug False para produção
    app.run(host="0.0.0.0", port=port, debug=False)

# ==============================
# STARTUP
# ==============================
if __name__ == "__main__":
    # Carrega cache antes de iniciar bot
    load_cache()

    # Inicia Flask em thread separada
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Inicia reset loop em thread separada
    reset_thread = threading.Thread(target=reset_loop, daemon=True)
    reset_thread.start()

    # Inicia bot (bloqueante) - token vem do env
    if not BOT_TOKEN:
        logging.warning("[WARN] BOT_TOKEN vazio — bot Discord não será iniciado.")
        # Ainda mantém processo rodando mesmo sem bot: sleep loop para não finalizar o script
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            logging.info("Encerrando...")
    else:
        logging.info("[INICIANDO] Bot Discord...")
        try:
            bot.run(BOT_TOKEN)
        except Exception as e:
            logging.exception("[ERRO] falha ao iniciar bot: %s", e)
