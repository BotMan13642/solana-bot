
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
HELIUS_API_KEY = os.environ.get("HELIUS_API_KEY")
 
# Fail loudly on startup instead of silently breaking mid-run
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
# FILTERS V3 👑
# ============================================
MAX_MARKET_CAP = 500000
MIN_MARKET_CAP = 10000
MIN_LIQUIDITY = 15000
MIN_VOLUME_1H = 3000
MAX_AGE_MINUTES = 45
MIN_AGE_MINUTES = 5
MIN_PRICE_CHANGE = -10
MIN_TXNS = 30
MAX_TOP_HOLDER_PCT = 20      # No wallet over 20%
REQUIRE_SOCIALS = False      # Must have Twitter or Telegram
 
# ============================================
# SEEN COINS
# ============================================
seen_coins = set()
 
def send_telegram(message, important=False):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Send twice if super important
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
            pairs = [p for p in data["pairs"] if p.get("chainId") == "solana"]
            if pairs:
                return max(pairs, key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0))
        return None
    except Exception as e:
        print(f"Coin details error: {e}")
        return None
 
def get_token_info(token_address):
    """Get holder info from DEXScreener token page"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)
        data = response.json()
        return data
    except:
        return None
 
def check_socials(pair):
    """Check if coin has social links"""
    info = pair.get("info", {})
    socials = info.get("socials", [])
    websites = info.get("websites", [])
    
    has_twitter = any(s.get("type") == "twitter" for s in socials)
    has_telegram = any(s.get("type") == "telegram" for s in socials)
    has_website = len(websites) > 0
    
    return has_twitter, has_telegram, has_website
 
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
 
    # Market cap
    mc = float(pair.get("fdv", 0) or 0)
    if mc > MAX_MARKET_CAP:
        return False, [], f"MC too high ${mc:,.0f}"
    if mc < MIN_MARKET_CAP:
        return False, [], f"MC too low ${mc:,.0f}"
    reasons_passed.append(f"✅ MC ${mc:,.0f}")
 
    # Liquidity
    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
    if liquidity < MIN_LIQUIDITY:
        return False, [], f"Low liquidity ${liquidity:,.0f}"
    reasons_passed.append(f"✅ Liquidity ${liquidity:,.0f}")
 
    # Volume
    volume = float(pair.get("volume", {}).get("h1", 0) or 0)
    if volume < MIN_VOLUME_1H:
        return False, [], f"Low volume ${volume:,.0f}"
    reasons_passed.append(f"✅ Volume ${volume:,.0f}")
 
    # Transactions
    txns = pair.get("txns", {})
    buys = int(txns.get("h1", {}).get("buys", 0) or 0)
    sells = int(txns.get("h1", {}).get("sells", 0) or 0)
    total_txns = buys + sells
    if total_txns < MIN_TXNS:
        return False, [], f"Low txns {total_txns}"
    reasons_passed.append(f"✅ Txns {total_txns} (B:{buys} S:{sells})")
 
    # Buy pressure
    if sells > 0 and buys / sells < 0.8:
        return False, [], f"Too many sells B:{buys} S:{sells}"
    reasons_passed.append(f"✅ Buy pressure good")
 
    # Age
    age = get_age_minutes(pair)
    if age > MAX_AGE_MINUTES:
        return False, [], f"Too old {age:.0f}mins"
    if age < MIN_AGE_MINUTES:
        return False, [], f"Too new {age:.1f}mins"
    reasons_passed.append(f"✅ Age {age:.0f} mins")
 
    # Price not dumping
    price_change = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    if price_change < MIN_PRICE_CHANGE:
        return False, [], f"Dumping {price_change:.1f}%"
    reasons_passed.append(f"✅ Price {price_change:+.1f}%")
 
    # Socials check
    has_twitter, has_telegram, has_website = check_socials(pair)
    if REQUIRE_SOCIALS and not has_twitter and not has_telegram and not has_website:
        return False, [], "No socials - likely rug"
    social_str = ""
    if has_twitter: social_str += "🐦"
    if has_telegram: social_str += "✈️"
    if has_website: social_str += "🌐"
    if social_str:
        reasons_passed.append(f"✅ Socials {social_str}")
 
    return True, reasons_passed, "PASS"
 
def score_coin(pair):
    score = 5
 
    volume = float(pair.get("volume", {}).get("h1", 0) or 0)
    if volume > 50000: score += 2
    elif volume > 20000: score += 1
 
    txns = pair.get("txns", {})
    buys = int(txns.get("h1", {}).get("buys", 0) or 0)
    sells = int(txns.get("h1", {}).get("sells", 0) or 0)
    if sells > 0 and buys / sells > 1.5: score += 2
    elif sells > 0 and buys / sells > 1.2: score += 1
 
    price_change = float(pair.get("priceChange", {}).get("h1", 0) or 0)
    if price_change > 50: score += 1
    if price_change < 0: score -= 1
 
    has_twitter, has_telegram, has_website = check_socials(pair)
    if has_twitter and has_telegram: score += 1
 
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
    has_twitter, has_telegram, has_website = check_socials(pair)
 
    if score >= 8:
        preset = "PRESET 3 🔴 HOT"
        header = "🚨🚨🚨 HIGH SCORE ALERT 🚨🚨🚨"
    elif score >= 6:
        preset = "PRESET 2 🟡 WARM"
        header = "🚨 SCANNER PANDA ALERT"
    else:
        preset = "PRESET 1 🟢 COLD"
        header = "📡 SCANNER PANDA ALERT"
 
    social_links = ""
    info = pair.get("info", {})
    for s in info.get("socials", []):
        if s.get("type") == "twitter":
            social_links += f'🐦 <a href="{s.get("url")}">Twitter</a>  '
        if s.get("type") == "telegram":
            social_links += f'✈️ <a href="{s.get("url")}">Telegram</a>  '
    for w in info.get("websites", []):
        social_links += f'🌐 <a href="{w.get("url")}">Website</a>  '
 
    reasons_str = "\n".join(reasons_passed)
    dex_url = pair.get("url", "")
 
    message = f"""
{header}
{score_label} — Score: {score}/10
 
🪙 <b>{name} (${symbol})</b>
⏱ Age: {age:.0f} mins
 
💰 MC: ${mc:,.0f}
💧 Liquidity: ${liquidity:,.0f}
📊 1H Vol: ${volume:,.0f}
📈 1H Change: {price_change:+.1f}%
🔄 Buys: {buys} | Sells: {sells}
 
{social_links}
 
<b>Why it passed:</b>
{reasons_str}
 
🎯 Use: <b>{preset}</b>
 
📋 <code>{address}</code>
🔗 <a href="https://axiom.trade/t/{address}">Open in Axiom</a>
🔗 <a href="{dex_url}">DEXScreener</a>
"""
    return message, score >= 8
 
def run_bot():
    print("🐼 Scanner Panda V3 - KING MODE!")
    send_telegram("👑 Scanner Panda V3 LIVE!\n\n✅ Holder check ON\n✅ Socials check ON\n✅ Score system ON\n✅ HOT alerts x2 ping\n⚡ Every 5 seconds\n\nLet's get it! 🐼🔥")
 
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
                    print(f"✅ FOUND: {symbol} Score:{score}/10")
                    message, is_hot = format_alert(pair, reasons_passed)
                    send_telegram(message, important=is_hot)
                else:
                    symbol = pair.get("baseToken", {}).get("symbol", "???")
                    print(f"❌ {symbol}: {reason}")
 
            time.sleep(5)
 
        except Exception as e:
            print(f"Bot error: {e}")
            time.sleep(10)
 
if __name__ == "__main__":
    run_bot()
