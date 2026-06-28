import requests
import time
import json

# ============================================
# YOUR SETTINGS - EDIT THESE
# ============================================
TELEGRAM_TOKEN = "8701972089:AAG_41PCqfM1xJf9q40o90A0abTIBQk4Fzo"
TELEGRAM_CHAT_ID = "8420732989"
HELIUS_API_KEY = "78f177d6-fa97-426a-a963-79098e658927"

# ============================================
# YOUR FILTERS
# ============================================
MAX_MARKET_CAP = 500000      # $500K max
MIN_LIQUIDITY = 10000        # $10K min liquidity
MAX_DEV_HOLDING = 5          # Dev holding under 5%
MAX_TOP10_HOLDING = 30       # Top 10 holders under 30%
MIN_HOLDERS = 50             # At least 50 holders
REQUIRE_LP_BURNED = True     # LP must be burned

# ============================================
# SEEN COINS (avoid sending duplicates)
# ============================================
seen_coins = set()

def send_telegram(message):
    """Send alert to your Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_new_coins():
    """Fetch new migrated coins from DEXScreener"""
    try:
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        response = requests.get(url, timeout=10)
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"DEXScreener error: {e}")
        return []

def get_coin_details(token_address):
    """Get detailed info about a coin"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("pairs"):
            return data["pairs"][0]
        return None
    except Exception as e:
        print(f"Coin details error: {e}")
        return None

def check_filters(pair):
    """Run your filters on a coin"""
    try:
        # Market cap check
        mc = float(pair.get("fdv", 0) or 0)
        if mc > MAX_MARKET_CAP or mc < 1000:
            return False, "MC out of range"

        # Liquidity check
        liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        if liquidity < MIN_LIQUIDITY:
            return False, "Low liquidity"

        # Chain must be Solana
        if pair.get("chainId") != "solana":
            return False, "Not Solana"

        return True, "PASS"

    except Exception as e:
        return False, f"Filter error: {e}"

def format_alert(pair):
    """Format a nice Telegram message"""
    name = pair.get("baseToken", {}).get("name", "Unknown")
    symbol = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    mc = float(pair.get("fdv", 0) or 0)
    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    volume = float(pair.get("volume", {}).get("h1", 0) or 0)
    price_change = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    dex_url = pair.get("url", "")

    message = f"""
🚨 <b>NEW COIN ALERT</b> 🚨

🪙 <b>{name} (${symbol})</b>

💰 Market Cap: ${mc:,.0f}
💧 Liquidity: ${liquidity:,.0f}
📊 1H Volume: ${volume:,.0f}
📈 1H Change: {price_change:+.1f}%

📋 Contract:
<code>{address}</code>

🔗 <a href="{dex_url}">View on DEXScreener</a>
🔗 <a href="https://axiom.trade/t/{address}">Open in Axiom</a>

⚡ Run your 5 second check before buying!
"""
    return message

def run_bot():
    """Main bot loop"""
    print("🚀 Solana Bot Started!")
    print(f"Filters: MC under ${MAX_MARKET_CAP:,} | Liquidity over ${MIN_LIQUIDITY:,}")
    
    # Send startup message
    send_telegram("🤖 Your Solana Bot is LIVE! Watching for coins... 👀")
    
    while True:
        try:
            print("Scanning for new coins...")
            coins = get_new_coins()
            
            for coin in coins:
                # Only Solana coins
                if coin.get("chainId") != "solana":
                    continue
                    
                token_address = coin.get("tokenAddress", "")
                
                # Skip if already seen
                if token_address in seen_coins:
                    continue
                    
                seen_coins.add(token_address)
                
                # Get full details
                pair = get_coin_details(token_address)
                if not pair:
                    continue
                
                # Run filters
                passes, reason = check_filters(pair)
                
                if passes:
                    print(f"✅ FOUND: {pair.get('baseToken', {}).get('symbol')} - sending alert!")
                    message = format_alert(pair)
                    send_telegram(message)
                else:
                    print(f"❌ Filtered out: {reason}")
            
            # Wait 30 seconds before next scan
            print("Waiting 30 seconds...")
            time.sleep(30)
            
        except Exception as e:
            print(f"Bot error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    run_bot()
