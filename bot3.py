import os
import requests
import time
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
MIN_LIQUIDITY = 5000          # Min liquidity to confirm
MAX_MARKET_CAP = 500000       # Max MC
SCAN_INTERVAL = 15            # Scan every 15 seconds
MIN_VOLUME = 2000             # Min 1H volume

# ============================================
# TRACKING
# ============================================
seen_coins = set()
alerted_coins = set()

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

def get_pumpfun_trending():
    """Get trending coins from PumpFun"""
    try:
        url = "https://api.pumpfun.com/api/v1/coins"
        params = {
            "limit": 50,
            "sort": "trending",
            "timeframe": "1h"
        }
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception as e:
        print(f"PumpFun API error: {e}")
        return None

def get_coin_details_dexscreener(address):
    """Check if coin has migrated and get DEXScreener data"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            pairs = data.get("pairs", [])
            sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
            if sol_pairs:
                return max(sol_pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
        return None
    except Exception as e:
        print(f"DEXScreener error: {e}")
        return None

def format_pumpfun_alert(coin, stage, details=None):
    """Format PumpFun stage alert"""
    name = coin.get("name", "Unknown")
    symbol = coin.get("symbol", "???")
    address = coin.get("mint", "")
    
    if stage == "final_stretch":
        progress = coin.get("bonding_progress", 0)
        message = f"""
🟢 <b>PUMPFUN FINAL STRETCH</b> 🟢

🪙 <b>{name} (${symbol})</b>

📊 Bonding Progress: {progress:.1f}%
💰 MC: ${coin.get('market_cap', 0):,.0f}
💧 Liquidity: ${coin.get('liquidity', 0):,.0f}

⏰ About to migrate to Raydium!

📋 <code>{address}</code>
🔗 <a href="https://pump.fun/coin/{address}">View on PumpFun</a>

🎯 Use: <b>PRESET 1 🟢 COLD</b>
"""
        return message, False
    
    elif stage == "migrating":
        message = f"""
🟡 <b>MIGRATION IN PROGRESS!</b> 🟡

🪙 <b>{name} (${symbol})</b>

🔄 Just migrated from PumpFun!
⏱ Get in early before volume pumps!

📋 <code>{address}</code>
🔗 <a href="https://axiom.trade/t/{address}">Open in Axiom</a>

🎯 Use: <b>PRESET 2 🟡 WARM</b>
"""
        return message, True
    
    elif stage == "migrated":
        mc = float(details.get("fdv", 0) or 0)
        liquidity = float(details.get("liquidity", {}).get("usd", 0) or 0)
        volume = float(details.get("volume", {}).get("h1", 0) or 0)
        
        message = f"""
🔴 <b>MIGRATED TO RAYDIUM!</b> 🔴

🪙 <b>{name} (${symbol})</b>

💰 MC: ${mc:,.0f}
💧 Liquidity: ${liquidity:,.0f}
📊 1H Volume: ${volume:,.0f}

✅ Real on-chain data confirmed!

📋 <code>{address}</code>
🔗 <a href="https://axiom.trade/t/{address}">Open in Axiom</a>

🎯 Use: <b>PRESET 2 🟡 WARM</b>
"""
        return message, volume > 10000

def run_bot():
    print("🟢 PumpFun Scanner Bot LIVE!")
    send_telegram("🟢 <b>PumpFun Scanner Bot LIVE!</b>\n\n📡 Watching PumpFun bonding curves\n🔄 Detecting migrations to Raydium\n🚀 Catching coins EARLY!\n\nLet's find gems! 🎯")
    
    while True:
        try:
            print(f"Scanning PumpFun... {datetime.now().strftime('%H:%M:%S')}")
            
            # Get trending coins from PumpFun
            data = get_pumpfun_trending()
            if not data or "coins" not in data:
                print("No PumpFun data")
                time.sleep(SCAN_INTERVAL)
                continue
            
            coins = data.get("coins", [])
            
            for coin in coins:
                address = coin.get("mint", "")
                if not address or address in alerted_coins:
                    continue
                
                if address not in seen_coins:
                    seen_coins.add(address)
                    
                    progress = coin.get("bonding_progress", 0)
                    
                    # Stage 1: Final Stretch (80%+ progress)
                    if progress >= 80:
                        print(f"🟢 FINAL STRETCH: {coin.get('symbol')} at {progress:.0f}%")
                        message, _ = format_pumpfun_alert(coin, "final_stretch")
                        send_telegram(message)
                        alerted_coins.add(address)
                    
                    # Stage 2: Check if migrated
                    migrated_pair = get_coin_details_dexscreener(address)
                    if migrated_pair and address not in alerted_coins:
                        print(f"🟡 MIGRATED: {coin.get('symbol')}")
                        message, is_hot = format_pumpfun_alert(coin, "migrated", migrated_pair)
                        send_telegram(message, important=is_hot)
                        alerted_coins.add(address)
            
            print(f"Waiting {SCAN_INTERVAL} seconds...")
            time.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            print(f"Bot error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_bot()
