
import os
import asyncio
import json
import time
import requests
import websockets
from datetime import datetime, timezone
 
# ============================================
# YOUR SETTINGS — pulled from Railway env vars
# (Settings -> Variables on the Railway service)
# ============================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
 
_missing = [name for name, val in [
    ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
    ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
] if not val]
if _missing:
    raise RuntimeError(
        f"Missing required environment variable(s): {', '.join(_missing)}. "
        f"Set these in Railway under Settings -> Variables."
    )
 
# ============================================
# SETTINGS
# ============================================
PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
 
# Noise filter for brand-new token creation alerts.
# PumpFun gets hundreds of new tokens/hour, most are instant rugs.
# Raise this if you're getting flooded, lower it if you want more (noisier) signal.
MIN_INITIAL_BUY_SOL = 5.0
 
# ============================================
# TRACKING
# ============================================
alerted_new = set()
alerted_migration = set()
 
def send_telegram(message, important=False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
        if important:
            time.sleep(1)
            requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")
 
def format_new_token_alert(data):
    """Format alert for a brand-new PumpFun token creation"""
    name = data.get("name", "Unknown")
    symbol = data.get("symbol", "???")
    mint = data.get("mint", "")
    initial_buy = data.get("initialBuy", 0)
    market_cap_sol = data.get("marketCapSol", 0)
    sol_in_curve = data.get("vSolInBondingCurve", 0)
 
    message = f"""
🆕 <b>BRAND NEW PUMPFUN TOKEN</b> 🆕
 
🪙 <b>{name} (${symbol})</b>
 
💰 Market Cap: {market_cap_sol:.2f} SOL
🌊 SOL in Curve: {sol_in_curve:.2f}
🎯 Initial Buy: {initial_buy:,.0f} tokens
 
⏰ Just created — earliest possible signal!
 
📋 <code>{mint}</code>
🔗 <a href="https://pump.fun/coin/{mint}">View on PumpFun</a>
🔗 <a href="https://axiom.trade/t/{mint}">Open in Axiom</a>
 
🎯 Use: <b>PRESET 1 🟢 COLD</b>
"""
    return message
 
def format_migration_alert(data):
    """Format alert for a PumpFun -> Raydium migration"""
    mint = data.get("mint", "")
 
    message = f"""
🟡 <b>MIGRATED TO RAYDIUM!</b> 🟡
 
🪙 <b>Token Migration</b>
 
🔄 Just graduated from PumpFun bonding curve!
⏱ Get in early before volume pumps!
 
📋 <code>{mint}</code>
🔗 <a href="https://axiom.trade/t/{mint}">Open in Axiom</a>
🔗 <a href="https://dexscreener.com/solana/{mint}">DEXScreener</a>
 
🎯 Use: <b>PRESET 2 🟡 WARM</b>
"""
    return message
 
async def handle_message(raw_message):
    try:
        data = json.loads(raw_message)
    except Exception as e:
        print(f"Failed to parse message: {e}")
        return
 
    tx_type = data.get("txType", "")
    mint = data.get("mint", "")
 
    if not mint:
        return
 
    # New token creation event
    if tx_type == "create":
        if mint in alerted_new:
            return
        initial_buy_sol = data.get("solAmount", data.get("vSolInBondingCurve", 0))
        if initial_buy_sol and float(initial_buy_sol) < MIN_INITIAL_BUY_SOL:
            print(f"Skipping {data.get('symbol', '???')} - initial buy too small")
            return
        alerted_new.add(mint)
        symbol = data.get("symbol", "???")
        print(f"🆕 NEW TOKEN: {symbol}")
        message = format_new_token_alert(data)
        send_telegram(message)
 
    # Migration event (no txType="create", comes through subscribeMigration)
    elif "pool" in data or tx_type == "migrate":
        if mint in alerted_migration:
            return
        alerted_migration.add(mint)
        print(f"🟡 MIGRATED: {mint}")
        message = format_migration_alert(data)
        send_telegram(message, important=True)
 
async def run_bot():
    print("🟢 PumpFun Scanner Bot LIVE! (PumpPortal WebSocket)")
    send_telegram(
        "🟢 <b>PumpFun Scanner Bot LIVE!</b>\n\n"
        "📡 Streaming PumpFun token creation in real time\n"
        "🔄 Watching for migrations to Raydium\n"
        "🚀 Earliest possible signal — catching coins at birth!\n\n"
        "Let's find gems! 🎯"
    )
 
    reconnect_delay = 5
 
    while True:
        try:
            async with websockets.connect(PUMPPORTAL_WS) as ws:
                print(f"Connected to PumpPortal... {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
                reconnect_delay = 5  # reset backoff on successful connect
 
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
 
                async for raw_message in ws:
                    await handle_message(raw_message)
 
        except Exception as e:
            print(f"WebSocket error: {e}. Reconnecting in {reconnect_delay}s...")
            send_telegram(f"⚠️ PumpFun bot disconnected, reconnecting... ({e})")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)  # exponential backoff, capped at 60s
 
if __name__ == "__main__":
    asyncio.run(run_bot())
