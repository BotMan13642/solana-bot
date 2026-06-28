
Bot · PY
import requests
import time
from datetime import datetime, timezone
 
# ============================================
# YOUR SETTINGS
# ============================================
TELEGRAM_TOKEN = "8701972089:AAG_41PCqfM1xJf9q40o90A0abTIBQk4Fzo"
TELEGRAM_CHAT_ID = "8420732989"
HELIUS_API_KEY = "78f177d6-fa97-426a-a963-79098e658927"
 
# ============================================
# FILTERS - UPGRADED 🔥
# ============================================
MAX_MARKET_CAP = 500000       # $500K max
MIN_MARKET_CAP = 10000        # $10K min (ignore micro rugs)
MIN_LIQUIDITY = 15000         # $15K min liquidity
MAX_TOP10_HOLDING = 20        # Top 10 holders under 20%
MIN_HOLDERS = 50              # At least 50 holders
MIN_VOLUME_1H = 5000          # At least $5K volume in last hour
MAX_AGE_MINUTES = 45          # Not older than 45 mins
MIN_AGE_MINUTES = 5           # Not newer than 5 mins
MIN_PRICE_CHANGE = -10        # Not already dumping more than -10%
MIN_TXNS = 50                 # At least 50 transactions
 
# ============================================
# SEEN COINS
# ============================================
seen_coins = set()
 
def send_telegram(message):
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
 
def get_new_coins():
    try:
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        response = requests.get(url, timeout=10)
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"DEXScreener error: {e}")
        return []
 
def get_coin_details(token_address):
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("pairs"):
            # Return the pair with highest liquidity
            pairs = [p for p in data["pairs"] if p.get("chainId") == "solana"]
            if pairs:
                return max(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
        return None
    except Exception as e:
        print(f"Coin details error: {e}")
        return None
 
def get_age_minutes(pair):
    try:
        created = pair.get("pairCreatedAt")
        if created:
            created_time = datetime.fromtimestamp(created / 1000, tz=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            age = (now - created_time).total_seconds() / 60
            return age
        return 999
    except:
        return 999
 
def check_filters(pair):
    reasons_passed = []
    
    # Chain must be Solana
    if pair.get("chainId") != "solana":
        return False, [], "Not Solana"
 
    # Market cap check
    mc = float(pair.get("fdv", 0) or 0)
    if mc > MAX_MARKET_CAP:
        return False, [], f"MC too high ${mc:,.0f}"
    if mc < MIN_MARKET_CAP:
        return False, [], f"MC too low ${mc:,.0f}"
    reasons_passed.append(f"✅ MC ${mc:,.0f}")
 
    # Liquidity check
    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    if liquidity < MIN_LIQUIDITY:
        return False, [], f"Low liquidity ${liquidity:,.0f}"
    reasons_passed.append(f"✅ Liquidity ${liquidity:,.0f}")
 
    # Volume check
    volume = float(pair.get("volume", {}).get("h1", 0) or 0)
    if volume < MIN_VOLUME_1H:
        return False, [], f"Low volume ${volume:,.0f}"
    reasons_passed.append(f"✅ Volume ${volume:,.0f}")
 
    # Transaction check
    txns = pair.get("txns", {})
    buys = int(txns.get("h1", {}).get("buys", 0) or 0)
    sells = int(txns.get("h1", {}).get("sells", 0) or 0)
    total_txns = buys + sells
    if total_txns < MIN_TXNS:
        return False, [], f"Low txns {total_txns}"
    reasons_passed.append(f"✅ Txns {total_txns} (B:{buys} S:{sells})")
 
    # Buy/sell ratio - more buys than sells is good
    if sells > 0 and buys / sells < 0.8:
        return False, [], f"Too many sells (B:{buys} S:{sells})"
    reasons_passed.append(f"✅ Buy pressure good")
 
    # Age check
    age = get_age_minutes(pair)
    if age > MAX_AGE_MINUTES:
        return False, [], f"Too old {age:.0f}mins"
    if age < MIN_AGE_MINUTES:
        return False, [], f"Too new {age:.1f}mins"
    reasons_passed.append(f"✅ Age {age:.0f} mins")
 
    # Price change - not already dumping
    price_change = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    if price_change < MIN_PRICE_CHANGE:
        return False, [], f"Dumping {price_change:.1f}%"
    reasons_passed.append(f"✅ Price {price_change:+.1f}%")
 
    return True, reasons_passed, "PASS"
 
def score_coin(pair):
    """Give coin a score 1-10 based on how good it looks"""
    score = 5
    
    # Volume boost
    volume = float(pair.get("volume", {}).get("h1", 0) or 0)
    if volume > 50000: score += 2
    elif volume > 20000: score += 1
 
    # Buy pressure boost
    txns = pair.get("txns", {})
    buys = int(txns.get("h1", {}).get("buys", 0) or 0)
    sells = int(txns.get("h1", {}).get("sells", 0) or 0)
    if sells > 0 and buys / sells > 1.5: score += 2
    elif sells > 0 and buys / sells > 1.2: score += 1
 
    # Price momentum
    price_change = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    if price_change > 50: score += 1
    if price_change < 0: score -= 1
 
    return min(10, max(1, score))
 
def get_score_emoji(score):
    if score >= 8: return "🔥🔥🔥 HOT"
    if score >= 6: return "🟡 WARM"
    return "🟢 COLD"
 
def format_alert(pair, reasons_passed):
    name = pair.get("baseToken", {}).get("name", "Unknown")
    symbol = pair.get("baseToken", {}).get("symbol", "???")
    address = pair.get("baseToken", {}).get("address", "")
    mc = float(pair.get("fdv", 0) or 0)
    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    volume = float(pair.get("volume", {}).get("h1", 0) or 0)
    price_change = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    txns = pair.get("txns", {})
    buys = int(txns.get("h1", {}).get("buys", 0) or 0)
    sells = int(txns.get("h1", {}).get("sells", 0) or 0)
    age = get_age_minutes(pair)
    score = score_coin(pair)
    score_label = get_score_emoji(score)
    dex_url = pair.get("url", "")
 
    # Pick preset based on score
    if score >= 8:
        preset = "PRESET 3 🔴 HOT"
    elif score >= 6:
        preset = "PRESET 2 🟡 WARM"
    else:
        preset = "PRESET 1 🟢 COLD"
 
    reasons_str = "\n".join(reasons_passed)
 
    message = f"""
🚨 <b>SCANNER PANDA ALERT</b> 🚨
{score_label} — Score: {score}/10
 
🪙 <b>{name} (${symbol})</b>
⏱ Age: {age:.0f} mins
 
💰 MC: ${mc:,.0f}
💧 Liquidity: ${liquidity:,.0f}
📊 1H Vol: ${volume:,.0f}
📈 1H Change: {price_change:+.1f}%
🔄 Buys: {buys} | Sells: {sells}
 
<b>Why it passed:</b>
{reasons_str}
 
🎯 Use: <b>{preset}</b>
 
📋 <code>{address}</code>
 
🔗 <a href="https://axiom.trade/t/{address}">Open in Axiom</a>
🔗 <a href="{dex_url}">DEXScreener</a>
"""
    return message
 
def run_bot():
    print("🚀 Scanner Panda UPGRADED - Started!")
    send_telegram("🐼 Scanner Panda UPGRADED is LIVE!\n\n⚡ Scanning every 5 seconds\n🔥 Strict filters ON\n📊 Scoring system ON\n\nLet's get it! 🚀")
    
    while True:
        try:
            print(f"Scanning... {datetime.now().strftime('%H:%M:%S')}")
            coins = get_new_coins()
            
            for coin in coins:
                if coin.get("chainId") != "solana":
                    continue
                    
                token_address = coin.get("tokenAddress", "")
                if not token_address or token_address in seen_coins:
                    continue
                    
                seen_coins.add(token_address)
                
                pair = get_coin_details(token_address)
                if not pair:
                    continue
                
                passes, reasons_passed, reason = check_filters(pair)
                
                if passes:
                    symbol = pair.get("baseToken", {}).get("symbol", "???")
                    score = score_coin(pair)
                    print(f"✅ FOUND: {symbol} Score:{score}/10 - sending alert!")
                    message = format_alert(pair, reasons_passed)
                    send_telegram(message)
                else:
                    symbol = pair.get("baseToken", {}).get("symbol", "???")
                    print(f"❌ {symbol}: {reason}")
            
            # Scan every 5 seconds
            time.sleep(5)
            
        except Exception as e:
            print(f"Bot error: {e}")
            time.sleep(10)
 
if __name__ == "__main__":
    run_bot()
 


