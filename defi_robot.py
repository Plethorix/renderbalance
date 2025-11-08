from flask import Flask, jsonify
from flask_cors import CORS  # <-- añadido
from web3 import Web3
import sqlite3
import threading
import time
from datetime import datetime
import os

# ----------------------------
# 🔌 Configuración
# ----------------------------
USER = os.getenv("USER_ADDRESS", "0x0000000000000000000000000000000000000000")
INTERVALO = 300  # segundos
DB_FILE = "defi_data.db"
LIMIT_REGISTROS = 10000

# ----------------------------
# 🌐 RPCs de respaldo (failover)
# ----------------------------
RPC_ENDPOINTS = [
    "https://arbitrum-one-rpc.publicnode.com",
    "https://arb1.arbitrum.io/rpc",
    "https://1rpc.io/arb",
    "https://rpc.ankr.com/arbitrum",
    "https://arbitrum.meowrpc.com"
]

current_rpc_index = 0
provider = Web3(Web3.HTTPProvider(RPC_ENDPOINTS[current_rpc_index]))

def switch_rpc():
    global current_rpc_index, provider
    current_rpc_index = (current_rpc_index + 1) % len(RPC_ENDPOINTS)
    provider = Web3(Web3.HTTPProvider(RPC_ENDPOINTS[current_rpc_index]))
    print(f"🔄 Cambiando RPC a: {RPC_ENDPOINTS[current_rpc_index]}")

# ----------------------------
# 🏦 Direcciones de contratos (checksum)
# ----------------------------
MORPHO_DEFAULT = Web3.to_checksum_address("0x7e97fa6893871A2751B5fE961978DCCb2c201E65")
AAVE_USDC = Web3.to_checksum_address("0x724dc807b04555b71ed48a6896b6f41593b8c637")
AAVE_DEBT = Web3.to_checksum_address("0xf611aeb5013fd2c0511c9cd55c7dc5c1140741a6")
EULER_USDC_EARN = Web3.to_checksum_address("0xe4783824593a50Bfe9dc873204CEc171ebC62dE0")

ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "type": "function"},
    {"constant": True, "inputs": [{"name": "shares", "type": "uint256"}],
     "name": "convertToAssets", "outputs": [{"name": "", "type": "uint256"}],
     "type": "function"}
]

# ----------------------------
# 🗄️ Base de datos SQLite
# ----------------------------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS defi_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    address TEXT,
    morpho REAL,
    aave REAL,
    euler REAL,
    debt REAL,
    net REAL
)
""")
conn.commit()

# ----------------------------
# ⚙️ Funciones de utilidad
# ----------------------------
def get_balances(user_address):
    try:
        user_address = Web3.to_checksum_address(user_address)

        morpho = provider.eth.contract(address=MORPHO_DEFAULT, abi=ERC20_ABI)
        aave = provider.eth.contract(address=AAVE_USDC, abi=ERC20_ABI)
        debt = provider.eth.contract(address=AAVE_DEBT, abi=ERC20_ABI)
        euler = provider.eth.contract(address=EULER_USDC_EARN, abi=ERC20_ABI)

        morpho_shares = morpho.functions.balanceOf(user_address).call()
        morpho_assets = morpho.functions.convertToAssets(morpho_shares).call()
        aave_balance = aave.functions.balanceOf(user_address).call()
        debt_balance = debt.functions.balanceOf(user_address).call()
        euler_shares = euler.functions.balanceOf(user_address).call()
        euler_assets = euler.functions.convertToAssets(euler_shares).call()

        morpho_usdc = morpho_assets / 1e6
        aave_usdc = aave_balance / 1e6
        debt_usdc = debt_balance / 1e6
        euler_usdc = euler_assets / 1e6
        net = morpho_usdc + aave_usdc + euler_usdc - debt_usdc

        return {
            "morpho": morpho_usdc,
            "aave": aave_usdc,
            "euler": euler_usdc,
            "debt": debt_usdc,
            "net": net
        }
    except Exception as e:
        print(f"⚠️ Error con RPC {RPC_ENDPOINTS[current_rpc_index]}: {e}")
        switch_rpc()
        time.sleep(2)
        return get_balances(user_address)

def save_snapshot(address, balances):
    cursor.execute("""
        INSERT INTO defi_snapshots (timestamp, address, morpho, aave, euler, debt, net)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        address,
        balances["morpho"],
        balances["aave"],
        balances["euler"],
        balances["debt"],
        balances["net"]
    ))
    conn.commit()

def cleanup_database(limit=LIMIT_REGISTROS):
    cursor.execute("SELECT COUNT(*) FROM defi_snapshots")
    count = cursor.fetchone()[0]
    if count > limit:
        cursor.execute("""
            DELETE FROM defi_snapshots
            WHERE id NOT IN (
                SELECT id FROM defi_snapshots
                ORDER BY id DESC
                LIMIT ?
            )
        """, (limit,))
        conn.commit()
        print(f"🧹 Base de datos limpiada (se mantienen {limit} registros más recientes)")

# ----------------------------
# 🤖 Hilo del robot
# ----------------------------
def robot_loop():
    while True:
        try:
            balances = get_balances(USER)
            save_snapshot(USER, balances)
            cleanup_database()
            print(f"[{datetime.utcnow().isoformat()}] Snapshot guardado correctamente.")
        except Exception as e:
            print("⚠️ Error en robot:", e)
        time.sleep(INTERVALO)

threading.Thread(target=robot_loop, daemon=True).start()

# ----------------------------
# 🌐 API Flask
# ----------------------------
app = Flask(__name__)
CORS(app)  # <-- Permite CORS desde cualquier dominio
# Para restringir: CORS(app, origins=["https://renderonbalance.edgeone.app"])

@app.route("/api/latest", methods=["GET"])
def latest_snapshot():
    cursor.execute("SELECT * FROM defi_snapshots ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        keys = ["id", "timestamp", "address", "morpho", "aave", "euler", "debt", "net"]
        return jsonify(dict(zip(keys, row)))
    return jsonify({"error": "No hay datos aún"}), 404

@app.route("/api/history", methods=["GET"])
def history():
    cursor.execute("SELECT * FROM defi_snapshots ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()
    keys = ["id", "timestamp", "address", "morpho", "aave", "euler", "debt", "net"]
    return jsonify([dict(zip(keys, r)) for r in rows])

@app.route("/api/stats", methods=["GET"])
def stats():
    cursor.execute("""
        SELECT AVG(net), MIN(net), MAX(net)
        FROM (SELECT net FROM defi_snapshots ORDER BY id DESC LIMIT 1000)
    """)
    row = cursor.fetchone()
    if row:
        avg_net, min_net, max_net = row
        variation = (max_net - min_net) if avg_net else 0
        return jsonify({
            "average_net": avg_net,
            "min_net": min_net,
            "max_net": max_net,
            "variation": variation
        })
    return jsonify({"error": "No hay datos suficientes"}), 404

if __name__ == "__main__":
    print("🚀 Iniciando robot + API Flask en http://0.0.0.0:5000 ...")
    app.run(host="0.0.0.0", port=5000)
