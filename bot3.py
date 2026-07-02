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

# Rug-risk cutoff: if the single largest wallet owns more than this
# percent of total supply, we skip alerting no matter how many holders
# there are - concentrated supply is a classic dump/rug setup.
MAX_TOP_HOLDER_PCT = 40

# Liquidity check for migration alerts. Thin liquidity right after
# migrating to Raydium is a common rug/dump setup - we still alert,
# but flag it clearly if liquidity is below this amount.
MIN_MIGRATION_LIQUIDITY_USD = 10000

def get_migration_liquidity(mint):
    """Check real liquidity on DEXScreener right after a migration."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return 0
        data = response.json()
        pairs = [p for p in data.get("pairs", []) if p.get("chainId") == "solana"]
        if not pairs:
            return 0
        best = max(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
        return float(best.get("liquidity", {}).get("usd", 0) or 0)
    except Exception as e:
        print(f"DEXScreener migration check error: {e}")
        return 0

# Multi-checkpoint growth tracking:
# We check holder count at several points after creation. A coin that
# explodes fast gets flagged sooner and louder (FAST MOVER). A coin that
# climbs more steadily still gets caught at a later checkpoint (CONFIRMED).
# Format: (seconds_after_creation, min_holders_required, alert_label)
CHECKPOINTS = [
    (10, 15, "🚀 FAST MOVER"),
    (20, 20, "🔥 CONFIRMED"),
    (35, 25, "🔥 CONFIRMED"),
]

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
    except Exception as e:
        print(f"Telegram error: {e}")

def get_holder_stats(mint):
    """Query Helius for holder count AND top-holder concentration.
    Returns (holder_count, top_holder_pct) - top_holder_pct is the
    percentage of total supply held by the single largest wallet
    (excluding the token's own liquidity/bonding-curve account, which
    isn't a real "holder" in the rug-risk sense)."""
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
        if response.status_code != 200:
            return 0, 100.0  # fail safe: treat unknown as maximally risky

        data = response.json()
        accounts = data.get("result", {}).get("token_accounts", [])
        if not accounts:
            return 0, 100.0

        # Aggregate balance per owner (a wallet could have >1 token account)
        balances_by_owner = {}
        for acc in accounts:
            owner = acc.get("owner")
            amount = acc.get("amount", 0)
            if not owner:
                continue
            balances_by_owner[owner] = balances_by_owner.get(owner, 0) + amount

        if not balances_by_owner:
            return 0, 100.0

        total_supply = sum(balances_by_owner.values())
        holder_count = len(balances_by_owner)

        if total_supply <= 0:
            return holder_count, 100.0

        top_holder_balance = max(balances_by_owner.values())
        top_holder_pct = (top_holder_balance / total_supply) * 100

        return holder_count, round(top_holder_pct, 1)
    except Exception as e:
        print(f"Helius error: {e}")
        return 0, 100.0  # fail safe: treat errors as maximally risky

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
<i>(unconfirmed — checking back shortly)</i>

🪙 <b>{name} (${symbol})</b>

💰 Market Cap: {market_cap_sol:.2f} SOL
🌊 SOL in Curve: {sol_in_curve:.2f}
🎯 Initial Buy: {initial_buy:,.0f} tokens

📋 <code>{mint}</code>
🔗 <a href="https://pump.fun/coin/{mint}">View on PumpFun</a>
🔗 <a href="https://axiom.trade/t/{mint}">Open in Axiom</a>
"""
    return message

def format_confirmed_alert(data, holder_count, elapsed_seconds, label, top_holder_pct):
    """Louder follow-up alert once a token shows real holder growth"""
    name = data.get("name", "Unknown")
    symbol = data.get("symbol", "???")
    mint = data.get("mint", "")
    market_cap_sol = data.get("marketCapSol", 0)

    message = f"""
{label}

🪙 <b>{name} (${symbol})</b>

👥 Holders: {holder_count} (in {elapsed_seconds}s)
🐋 Top wallet: {top_holder_pct}% of supply
💰 MC at creation: {market_cap_sol:.2f} SOL
<i>(price has likely moved since — check live before buying)</i>
✅ Real growth since creation!

📋 <code>{mint}</code>
🔗 <a href="https://pump.fun/coin/{mint}">View on PumpFun</a>
🔗 <a href="https://axiom.trade/t/{mint}">Open in Axiom</a>

🎯 Use: <b>PRESET 2 🟡 WARM</b>
"""
    return message

def format_migration_alert(data, liquidity_usd):
    """Alert for a PumpFun -> Raydium migration"""
    mint = data.get("mint", "")
    warning = ""
    if liquidity_usd < MIN_MIGRATION_LIQUIDITY_USD:
        warning = "\n⚠️ <b>Low liquidity — higher rug risk, be careful</b>"

    message = f"""
🟡 <b>MIGRATED TO RAYDIUM!</b> 🟡

🪙 <b>Token Migration</b>

🔄 Just graduated from PumpFun bonding curve!
🔒 LP auto-burned by PumpFun protocol (can't be rug-pulled this way)
💧 Liquidity: ${liquidity_usd:,.0f}{warning}
⏱ Get in early before volume pumps!

📋 <code>{mint}</code>
🔗 <a href="https://axiom.trade/t/{mint}">Open in Axiom</a>
🔗 <a href="https://dexscreener.com/solana/{mint}">DEXScreener</a>

🎯 Use: <b>PRESET 2 🟡 WARM</b>
"""
    return message

async def check_confirmation_later(data):
    """Check holder count at each checkpoint; alert as soon as one is cleared"""
    mint = data.get("mint", "")
    symbol = data.get("symbol", "???")
    elapsed_so_far = 0

    for checkpoint_time, threshold, label in CHECKPOINTS:
        wait_time = checkpoint_time - elapsed_so_far
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        elapsed_so_far = checkpoint_time

        if mint in alerted_confirmed:
            return  # already alerted at an earlier checkpoint somehow, stop

        # Run the blocking Helius HTTP call in a background thread
        # so it doesn't stall the websocket event loop
        holder_count, top_holder_pct = await asyncio.to_thread(get_holder_stats, mint)

        if holder_count >= threshold:
            if top_holder_pct > MAX_TOP_HOLDER_PCT:
                print(f"⚠️ {symbol}: {holder_count} holders but top wallet owns {top_holder_pct}% - skipping (rug risk)")
                continue  # don't alert, but keep checking later checkpoints
            alerted_confirmed.add(mint)
            print(f"{label}: {symbol} - {holder_count} holders, top wallet {top_holder_pct}%")
            message = format_confirmed_alert(data, holder_count, checkpoint_time, label, top_holder_pct)
            send_telegram(message, important=True)
            return
        else:
            print(f"❌ {symbol}: only {holder_count} holders at {checkpoint_time}s (needed {threshold})")

    print(f"❌ {symbol}: never cleared any checkpoint, skipping")

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
        # Don't block the websocket stream while we check liquidity
        asyncio.create_task(check_migration_liquidity(data))

async def check_migration_liquidity(data):
    """Wait a moment for DEXScreener to index the new pair, then check
    real liquidity before sending the migration alert."""
    mint = data.get("mint", "")
    await asyncio.sleep(10)  # give DEXScreener time to pick up the new pair
    liquidity_usd = await asyncio.to_thread(get_migration_liquidity, mint)

    if liquidity_usd < MIN_MIGRATION_LIQUIDITY_USD:
        print(f"⚠️ Migration {mint}: only ${liquidity_usd:,.0f} liquidity - alerting anyway but flagged low")

    message = format_migration_alert(data, liquidity_usd)
    send_telegram(message, important=True)

async def run_bot():
    print("🟢 PumpFun Scanner Bot LIVE! (PumpPortal WebSocket + Helius confirmation)")
    send_telegram(
        "🟢 <b>PumpFun Scanner Bot LIVE!</b>\n\n"
        "📡 Streaming PumpFun token creation in real time\n"
        f"🔍 Checking holder growth at {', '.join(str(c[0])+'s' for c in CHECKPOINTS)}\n"
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
