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
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY")
 
_missing = [name for name, val in [
    ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
    ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
    ("HELIUS_API_KEY", HELIUS_API_KEY),
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
HELIUS_RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
 
# Noise filter for the INSTANT creation alert.
# PumpFun gets hundreds of new tokens/hour, most are instant rugs.
MIN_INITIAL_BUY_SOL = 8.0
 
# Two-tier confirmation settings (this is the new part):
# Every qualifying token alerts instantly (tagged EARLY/unconfirmed) so
# you never miss anything. Then, after a short delay, we check real
# holder count and send a louder CONFIRMED alert only if it's showing
# real legs. Tune these two numbers once you've seen a few days of data.
CONFIRMATION_DELAY_SECONDS = 35
MIN_HOLDERS_CONFIRMED = 25
 
# ============================================
# TRACKING
# ============================================
alerted_new = set()
alerted_confirmed = set()
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
 
def get_holder_count(mint):
    """Query Helius for current holder count of a token mint"""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": "panda-scanner",
            "method": "getTokenAccounts",
            "params": {
                "mint": mint,
                "limit": 1000
            }
        }
        response = requests.post(HELIUS_RPC_URL, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            accounts = data.get("result", {}).get("token_accounts", [])
            owners = set(acc.get("owner") for acc in accounts if acc.get("owner"))
            return len(owners)
        return 0
    except Exception as e:
        print(f"Helius error: {e}")
        return 0
 
def format_new_token_alert(data):
    """Instant, unconfirmed alert for a brand-new PumpFun token creation"""
    name = data.get("name", "Unknown")
    symbol = data.get("symbol", "???")
    mint = data.get("mint", "")
    initial_buy = data.get("initialBuy", 0)
    market_cap_sol = data.get("marketCapSol", 0)
    sol_in_curve = data.get("vSolInBondingCurve", 0)
 
    message = f"""
🟢 <b>EARLY — JUST CREATED</b> 🟢
<i>(unconfirmed — checking back in {CONFIRMATION_DELAY_SECONDS}s)</i>
 
🪙 <b>{name} (${symbol})</b>
 
💰 Market Cap: {market_cap_sol:.2f} SOL
🌊 SOL in Curve: {sol_in_curve:.2f}
🎯 Initial Buy: {initial_buy:,.0f} tokens
 
📋 <code>{mint}</code>
🔗 <a href="https://pump.fun/coin/{mint}">View on PumpFun</a>
🔗 <a href="https://axiom.trade/t/{mint}">Open in Axiom</a>
"""
    return message
 
def format_confirmed_alert(data, holder_count):
    """Louder follow-up alert once a token shows real holder growth"""
    name = data.get("name", "Unknown")
    symbol = data.get("symbol", "???")
    mint = data.get("mint", "")
 
    message = f"""
🔥 <b>CONFIRMED — SHOWING LEGS</b> 🔥
 
🪙 <b>{name} (${symbol})</b>
 
👥 Holders: {holder_count} (in under {CONFIRMATION_DELAY_SECONDS}s)
✅ Real growth since creation!
 
📋 <code>{mint}</code>
🔗 <a href="https://pump.fun/coin/{mint}">View on PumpFun</a>
🔗 <a href="https://axiom.trade/t/{mint}">Open in Axiom</a>
 
🎯 Use: <b>PRESET 2 🟡 WARM</b>
"""
    return message
 
def format_migration_alert(data):
    """Alert for a PumpFun -> Raydium migration"""
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
 
async def check_confirmation_later(data):
    """Wait, then check holder count and send a confirmed alert if it qualifies"""
    mint = data.get("mint", "")
    await asyncio.sleep(CONFIRMATION_DELAY_SECONDS)
 
    # Run the blocking Helius HTTP call in a background thread
    # so it doesn't stall the websocket event loop
    holder_count = await asyncio.to_thread(get_holder_count, mint)
 
    if holder_count >= MIN_HOLDERS_CONFIRMED and mint not in alerted_confirmed:
        alerted_confirmed.add(mint)
        symbol = data.get("symbol", "???")
        print(f"🔥 CONFIRMED: {symbol} - {holder_count} holders")
        message = format_confirmed_alert(data, holder_count)
        send_telegram(message, important=True)
    else:
        print(f"❌ {data.get('symbol', '???')}: only {holder_count} holders, skipping confirmation")
 
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
        print(f"🆕 NEW TOKEN (silent, watching): {symbol}")
        # No instant Telegram alert anymore — too much 0-holder noise.
        # We still track it in the background and only alert once
        # it actually proves itself via check_confirmation_later().
 
        # Schedule the follow-up holder-count check without blocking
        # the rest of the websocket stream
        asyncio.create_task(check_confirmation_later(data))
 
    # Migration event (no txType="create", comes through subscribeMigration)
    elif "pool" in data or tx_type == "migrate":
        if mint in alerted_migration:
            return
        alerted_migration.add(mint)
        print(f"🟡 MIGRATED: {mint}")
        message = format_migration_alert(data)
        send_telegram(message, important=True)
 
async def run_bot():
    print("🟢 PumpFun Scanner Bot LIVE! (PumpPortal WebSocket + Helius confirmation)")
    send_telegram(
        "🟢 <b>PumpFun Scanner Bot LIVE!</b>\n\n"
        "📡 Streaming PumpFun token creation in real time\n"
        f"🔍 Confirming holder growth after {CONFIRMATION_DELAY_SECONDS}s\n"
        "🔄 Watching for migrations to Raydium\n\n"
        "Let's find gems! 🎯"
    )
 
    reconnect_delay = 5
 
    while True:
        try:
            async with websockets.connect(PUMPPORTAL_WS) as ws:
                print(f"Connected to PumpPortal... {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
                reconnect_delay = 5
 
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
 
                async for raw_message in ws:
                    await handle_message(raw_message)
 
        except Exception as e:
            print(f"WebSocket error: {e}. Reconnecting in {reconnect_delay}s...")
            send_telegram(f"⚠️ PumpFun bot disconnected, reconnecting... ({e})")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
 
if __name__ == "__main__":
    asyncio.run(run_bot())
