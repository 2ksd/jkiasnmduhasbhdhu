from flask import Flask, jsonify
import threading
import time
import requests

app = Flask(__name__)

# ==========================
# CONFIGURAÇÕES
# ==========================
SCAN_INTERVAL = 30  # segundos
MIN_PLAYERS = 1
MAX_PLAYERS = 12
LAST_JOBIDS = []

# ==========================
# FUNÇÃO QUE FAZ O SCAN
# ==========================
def scan_servers():
    global LAST_JOBIDS

    while True:
        try:
            # Roblox endpoint oficial de servidores públicos
            url = "https://games.roblox.com/v1/games/10901838679/servers/Public?sortOrder=Asc&limit=100"

            r = requests.get(url, timeout=10)
            data = r.json()

            servers = data.get("data", [])

            # filtrar jobIds pelo número de players desejado
            job_ids = [
                srv["id"]
                for srv in servers
                if srv.get("playing", 0) >= MIN_PLAYERS and srv.get("playing", 0) <= MAX_PLAYERS
            ]

            LAST_JOBIDS = job_ids
            print(f"[SCAN] Encontrados {len(job_ids)} servidores.")

        except Exception as e:
            print("Erro no scan:", e)

        time.sleep(SCAN_INTERVAL)

# ==========================
# ENDPOINTS
# ==========================

@app.route("/")
def root():
    return jsonify({"status": "online", "servers_found": len(LAST_JOBIDS)})

@app.route("/jobs")
def jobs():
    return jsonify({
        "count": len(LAST_JOBIDS),
        "servers": LAST_JOBIDS
    })

# ==========================
# INICIAR SCAN AUTOMÁTICO
# ==========================

def start_background_thread():
    t = threading.Thread(target=scan_servers, daemon=True)
    t.start()

start_background_thread()

# ==========================
# VERCEL HANDLER
# ==========================

def handler(req, res):
    return app(req, res)

if __name__ == "__main__":
    app.run(debug=True)
