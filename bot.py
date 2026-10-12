import os, re, sqlite3, requests, asyncio, logging, json, threading
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil import parser as date_parser
from flask import Flask

# --- Web server for Render Free ---
app = Flask(__name__)
@app.route('/')
def home(): return "Listing Bot V2 Optimized - Running"
def run_web():
    try:
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
    except: pass
threading.Thread(target=run_web, daemon=True).start()

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = Bot(token=BOT_TOKEN)
scheduler = AsyncIOScheduler()
logging.basicConfig(level=logging.INFO)

conn = sqlite3.connect('listings.db', check_same_thread=False)
conn.execute('CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY)')
def is_sent(_id): return conn.execute('SELECT 1 FROM sent WHERE id=?', (_id,)).fetchone() is not None
def mark_sent(_id):
    try: conn.execute('INSERT OR IGNORE INTO sent VALUES (?)', (_id,)); conn.commit()
    except: pass

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

def safe_json_get(url, headers=None, timeout=15):
    try:
        r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        if r.status_code != 200 or not r.text.strip(): return None
        return r.json()
    except: return None

# --- TỐI ƯU THÔNG TIN TOKEN ---
def extract_contract(text):
    # 0x... EVM, Solana base58 32-44 chars
    evm = re.search(r'0x[a-fA-F0-9]{40}', text)
    if evm: return evm.group(0)
    sol = re.search(r'[1-9A-HJ-NP-Za-km-z]{32,44}', text)
    # tránh nhầm id thường
    if sol and len(sol.group(0)) > 32: return sol.group(0)
    return None

def extract_website(text):
    m = re.search(r'(https?://[^\s\)"]+\.(?:com|io|xyz|app|network|org|finance)[^\s\)"]*)', text, re.I)
    if m:
        url = m.group(1)
        if 'binance.com' not in url and 'okx.com' not in url and 'mexc.com' not in url: # loại link sàn
            return url
    return None

def get_token_info(symbol):
    """Lấy contract, website, CMC từ CoinGecko"""
    info = {"contract": None, "website": None, "cmc": None, "listed_on": []}
    try:
        # 1. Search coingecko
        s = safe_json_get(f"https://api.coingecko.com/api/v3/search?query={symbol}")
        if not s or not s.get('coins'): return info
        # lấy coin khớp symbol nhất
        coin = next((c for c in s['coins'] if c['symbol'].lower() == symbol.lower()), s['coins'][0])
        coin_id = coin['id']
        
        # 2. Detail để lấy contract, website, cmc không có nhưng có link
        d = safe_json_get(f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=true&market_data=false&community_data=false&developer_data=false")
        if d:
            platforms = d.get('platforms', {})
            # ưu tiên EVM
            for plat in ['ethereum', 'bnb-smart-chain', 'arbitrum-one', 'base', 'polygon-pos', 'solana']:
                if platforms.get(plat): 
                    info['contract'] = platforms[plat]
                    break
            if not info['contract']:
                for v in platforms.values():
                    if v: 
                        info['contract'] = v
                        break
            links = d.get('links', {})
            if links.get('homepage') and links['homepage'][0]:
                info['website'] = links['homepage'][0]
            # tickers = các sàn đã list
            tickers = d.get('tickers', [])[:30]
            exchanges = list(set([t['market']['name'] for t in tickers if t.get('market')]))
            info['listed_on'] = exchanges[:8]

        # 3. CMC link
        info['cmc'] = f"https://coinmarketcap.com/currencies/{coin_id}/" if coin_id else f"https://coinmarketcap.com/search/?query={symbol}"
    except Exception as e:
        logging.warning(f"get_token_info {symbol} fail {e}")
    return info

def parse_token_symbol(title):
    # Ưu tiên (TOKEN) hoặc $TOKEN
    m = re.search(r'\(([A-Z0-9]{2,12})\)', title)
    if m: return m.group(1)
    m = re.search(r'\$([A-Z]{2,12})\b', title)
    if m: return m.group(1)
    m = re.search(r'\b([A-Z]{2,10})\b.*(?:will be listed|listing|to list)', title, re.I)
    if m: return m.group(1).upper()
    m = re.search(r'\b([A-Z]{2,10})\b', title)
    return m.group(1) if m else "NEW"

async def send_optimized(exchange, token, title, link, trade_time=None, raw_content=""):
    # Chỉ gửi khi mới
    if is_sent(f"{exchange}_{token}_{link}") : return
    
    # Lấy thêm thông tin
    contract_from_ann = extract_contract(raw_content + " " + title)
    web_from_ann = extract_website(raw_content)
    
    token_info = get_token_info(token) if token != "NEW" else {"contract": contract_from_ann, "website": web_from_ann, "cmc": None, "listed_on": []}
    
    contract = contract_from_ann or token_info.get('contract')
    website = web_from_ann or token_info.get('website')
    cmc = token_info.get('cmc') or f"https://coinmarketcap.com/search/?query={token}"
    listed = token_info.get('listed_on', [])

    # --- FORMAT GỌN, KHÔNG HASHTAG ---
    lines = []
    lines.append(f"🔔 <b>${token} | {exchange} LISTING</b>")
    lines.append("")
    lines.append(f"📰 {title[:180]}")
    if trade_time:
        try: lines.append(f"⏰ Trade: <b>{trade_time.strftime('%H:%M UTC - %d/%m')}</b>")
        except: pass
    lines.append("")
    if website: lines.append(f"🌐 <a href='{website}'>{website.replace('https://','')[:35]}</a>")
    if contract: lines.append(f"📜 <code>{contract[:6]}...{contract[-4:]}</code>")
    if cmc: lines.append(f"📊 <a href='{cmc}'>CoinMarketCap</a>")
    if listed:
        # lọc trùng sàn hiện tại
        other = [x for x in listed if exchange.lower() not in x.lower()][:5]
        if other: lines.append(f"🔄 Đã có trên: {', '.join(other)}")
    lines.append("")
    lines.append(f"🔗 <a href='{link}'>Announcement gốc</a>")

    msg = "\n".join(lines)
    
    # Fix link gốc sai -> thêm nút chuẩn
    buttons = [[InlineKeyboardButton("📄 Xem Ann Gốc", url=link)]]
    if website: buttons.append([InlineKeyboardButton("🌐 Website", url=website)])
    if cmc: buttons.append([InlineKeyboardButton("📊 CMC/CG", url=cmc)])

    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
        mark_sent(f"{exchange}_{token}_{link}")
        # hẹn T-10
        if trade_time:
            alert_time = trade_time - timedelta(minutes=10)
            if alert_time > datetime.utcnow() + timedelta(minutes=1):
                scheduler.add_job(lambda: asyncio.create_task(bot.send_message(chat_id=CHAT_ID, text=f"⏰ <b>SẮP TRADE 10P!</b>\n\n<b>${token}</b> trên {exchange} lúc {trade_time.strftime('%H:%M UTC')}\n<a href='{link}'>Chuẩn bị USDT!</a>", parse_mode='HTML')), 'date', run_date=alert_time)
    except Exception as e:
        logging.error(f"Send fail {e}")

# --- CHECK CÁC SÀN - ĐÃ FIX LINK GỐC ---
async def check_binance():
    try:
        data = safe_json_get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=5&catalogId=48")
        if not data: return
        for art in data.get('data',{}).get('catalogs',[{}])[0].get('articles',[]):
            _id = f"BINANCE_{art['code']}"
            if is_sent(_id): continue
            title = art['title']
            if "will list" not in title.lower() and "to list" not in title.lower(): continue
            detail = safe_json_get(f"https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode={art['code']}")
            content = json.dumps(detail) if detail else title
            token = parse_token_symbol(title)
            # LINK CHUẨN BINANCE
            link = f"https://www.binance.com/en/support/announcement/detail/{art['code']}"
            m = re.search(r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})', content)
            t = date_parser.parse(m.group(1)) if m else None
            await send_optimized("BINANCE", token, title, link, t, content)
            mark_sent(_id)
    except Exception as e: logging.error(f"Binance {e}")

async def check_okx():
    try:
        data = safe_json_get("https://www.okx.com/api/v5/support/announcement?annType=announcements-new-listings&page=1&pageSize=5")
        if not data: return
        for item in data.get('data',{}).get('announcements',[]):
            _id = f"OKX_{item['annId']}"
            if is_sent(_id): continue
            title = item['title']
            token = parse_token_symbol(title)
            # LINK CHUẨN OKX - fix lỗi sai link cũ
            p_url = item.get('url','')
            link = f"https://www.okx.com{'' if p_url.startswith('/') else '/help/'}"+p_url if p_url else f"https://www.okx.com/help/section/announcements"
            await send_optimized("OKX", token, title, link, None, title)
            mark_sent(_id)
    except Exception as e: logging.error(f"OKX {e}")

async def check_bybit():
    try:
        data = safe_json_get("https://api.bybit.com/v5/announcements?locale=en-US&type=new_crypto_listing&page=1&limit=5")
        if not data: return
        for item in data.get('result',{}).get('list',[]):
            _id = f"BYBIT_{item['id']}"
            if is_sent(_id): continue
            title = item['title']
            token = parse_token_symbol(title)
            link = item.get('url') or f"https://announcements.bybit.com/en-US/article/{item.get('id','')}"
            await send_optimized("BYBIT", token, title, link, None, item.get('description',''))
            mark_sent(_id)
    except Exception as e: logging.error(f"Bybit {e}")

async def check_gate():
    try:
        data = safe_json_get("https://www.gate.io/apiw/v2/cms/announcement/list?categroy_id=3&lang=en&page=1&page_size=5")
        if not data: return
        for art in data.get('data',{}).get('list',[]):
            _id = f"GATE_{art['id']}"
            if is_sent(_id): continue
            title = art['title']
            token = parse_token_symbol(title)
            link = f"https://www.gate.io/announcements/article/{art['id']}"
            await send_optimized("GATE", token, title, link, None, title)
            mark_sent(_id)
    except Exception as e: logging.error(f"Gate {e}")

async def check_mexc():
    try:
        data = safe_json_get("https://www.mexc.com/api/operation/announcement/list?announceType=1&pageNum=1&pageSize=5")
        if not data: return
        lst = data.get('data',{}).get('list',[]) or data.get('data',[])[:5]
        for art in lst:
            title = art.get('title','')
            _id = f"MEXC_{art.get('id', title)}"
            if is_sent(_id): continue
            if "will list" not in title.lower() and "listing" not in title.lower(): continue
            token = parse_token_symbol(title)
            link = f"https://www.mexc.com/news/{art.get('id','')}"
            await send_optimized("MEXC", token, title, link, None, title)
            mark_sent(_id)
    except Exception as e: logging.error(f"MEXC {e}")

async def check_bitget():
    try:
        data = safe_json_get("https://api.bitget.com/api/v2/public/annoucements?annType=new_cryptos&pageSize=5&languageType=0")
        if not data: return
        for art in (data.get('data') or {}).get('announcements',[]):
            if not isinstance(art, dict): continue
            _id = f"BITGET_{art.get('id')}"
            if is_sent(_id): continue
            title = art.get('annTitle','')
            token = parse_token_symbol(title)
            link = f"https://www.bitget.com/support/articles/{art.get('id')}"
            await send_optimized("BITGET", token, title, link, None, art.get('annDesc',''))
            mark_sent(_id)
    except Exception as e: logging.error(f"Bitget {e}")

async def check_kucoin():
    try:
        data = safe_json_get("https://www.kucoin.com/_api/cms/articles?category=new-listings&page=1&pageSize=5&lang=en_US")
        if not data: return
        items = data.get('items') or data.get('data',{}).get('items',[])
        for art in items:
            _id = f"KUCOIN_{art.get('id')}"
            if is_sent(_id): continue
            title = art.get('title','')
            token = parse_token_symbol(title)
            link = f"https://www.kucoin.com/announcement/{art.get('id')}"
            await send_optimized("KUCOIN", token, title, link, None, art.get('summary',''))
            mark_sent(_id)
    except Exception as e: logging.error(f"Kucoin {e}")

async def check_upbit():
    try:
        data = safe_json_get("https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=5&category=trade")
        if not data: return
        for art in data.get('data',{}).get('list',[]):
            _id = f"UPBIT_{art['id']}"
            if is_sent(_id): continue
            title = art['title']
            if "listing" not in title.lower() and "support" not in title.lower(): continue
            token = parse_token_symbol(title)
            link = f"https://upbit.com/service_center/notice?id={art['id']}"
            await send_optimized("UPBIT", token, title, link, None, title)
            mark_sent(_id)
    except Exception as e: logging.error(f"Upbit {e}")

async def main_loop():
    scheduler.start()
    logging.info("Bot V2 Optimized started")
    try: await bot.send_message(chat_id=CHAT_ID, text="✅ <b>Bot V2 Optimized đã online</b>\nFormat gọn - Có Contract/Web/CMC - Check sàn đã list", parse_mode='HTML')
    except: pass
    while True:
        try:
            await asyncio.gather(check_binance(), check_okx(), check_bybit(), check_gate(), check_mexc(), check_bitget(), check_kucoin(), check_upbit())
        except Exception as e: logging.error(f"Loop {e}")
        await asyncio.sleep(30) # check nhanh hơn, chỉ báo khi mới

if __name__ == "__main__":
    asyncio.run(main_loop())
