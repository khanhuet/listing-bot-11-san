
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
def home(): return "Bot is running! - 11 exchanges monitoring"
def run_web():
    try:
        app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
    except: pass
threading.Thread(target=run_web, daemon=True).start()
# -----------------------------------

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not BOT_TOKEN or not CHAT_ID:
    raise ValueError("Thieu TELEGRAM_BOT_TOKEN hoac CHAT_ID trong .env / Environment Variables")

bot = Bot(token=BOT_TOKEN)
scheduler = AsyncIOScheduler()
logging.basicConfig(level=logging.INFO)

# DB
conn = sqlite3.connect('listings.db', check_same_thread=False)
conn.execute('CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY)')
def is_sent(_id): return conn.execute('SELECT 1 FROM sent WHERE id=?', (_id,)).fetchone() is not None
def mark_sent(_id): 
    try:
        conn.execute('INSERT OR IGNORE INTO sent VALUES (?)', (_id,))
        conn.commit()
    except: pass

def ai_extract_token_and_time(title, content):
    if OPENAI_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_KEY)
            prompt = f"""Extract JSON from this crypto listing announcement. Title: {title} Content: {content[:3000]} Return ONLY JSON: {{"token": "SYMBOL", "trading_time_utc": "2026-05-20T10:00:00Z or null"}}"""
            resp = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role":"user","content":prompt}], temperature=0)
            data = json.loads(resp.choices[0].message.content)
            tt = date_parser.parse(data['trading_time_utc']) if data.get('trading_time_utc') else None
            return data.get('token'), tt
        except Exception as e:
            logging.warning(f"AI parser fail: {e}")
    token_match = re.search(r'\$?([A-Z]{2,10})\b', title)
    token = token_match.group(1) if token_match else "NEW"
    m = re.search(r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})', content)
    t = None
    try:
        if m: t = date_parser.parse(m.group(1))
    except: pass
    return token, t

async def send_channel(exchange, token, title, link, trade_time=None):
    emoji_map = {"BINANCE":"🟡","OKX":"⬛","MEXC":"💚","BITGET":"🔵","GATE":"⚪️","UPBIT":"🟠","KUCOIN":"🟢","BYBIT":"🟠","HTX":"🔴","BINGX":"🔷","LBANK":"🔶"}
    emoji = emoji_map.get(exchange, "💎")
    msg = f"{emoji} <b>[{exchange}] NEW LISTING</b>\n\n💎 <b>${token}</b>\n📢 {title}\n"
    if trade_time:
        try: msg += f"⏰ Trade: <b>{trade_time.strftime('%H:%M UTC %d/%m')}</b>\n"
        except: pass
    msg += f"\n#{token} #{exchange} #Listing"
    buttons = [[InlineKeyboardButton("📄 Announcement", url=link)]]
    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
    except Exception as e:
        logging.error(f"Send fail {e}")

async def send_t10_alert(exchange, token, trade_time):
    msg = f"⏰ <b>SẮP TRADE - 10P NỮA!</b>\n\n💎 <b>${token}</b> trên <b>{exchange}</b>\n🕐 Mở lúc {trade_time.strftime('%H:%M UTC')}\n👉 Chuẩn bị USDT!"
    try: await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
    except Exception as e: logging.error(f"T-10 fail {e}")

def schedule_t10(exchange, token, trade_time):
    if not trade_time: return
    try:
        alert_time = trade_time - timedelta(minutes=10)
        if alert_time > datetime.utcnow() + timedelta(minutes=1):
            scheduler.add_job(send_t10_alert, 'date', run_date=alert_time, args=[exchange, token, trade_time], id=f"{exchange}_{token}_{int(trade_time.timestamp())}_{os.urandom(2).hex()}")
            logging.info(f"Hẹn T-10 {token} {exchange} {alert_time}")
    except: pass

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept": "application/json"}

def safe_json_get(url, headers=None, timeout=15):
    try:
        r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        if r.status_code != 200 or not r.text.strip(): return None
        return r.json()
    except Exception as e:
        logging.warning(f"API warn {url[:60]} -> {e}")
        return None

async def check_binance():
    try:
        data = safe_json_get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=5&catalogId=48")
        if not data: return
        for art in data.get('data',{}).get('catalogs',[{}])[0].get('articles',[]):
            _id = f"BINANCE_{art['code']}"
            if is_sent(_id): continue
            title = art['title']
            if "will list" in title.lower() or "to list" in title.lower():
                detail = safe_json_get(f"https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode={art['code']}")
                content = str(detail) if detail else title
                token, ttime = ai_extract_token_and_time(title, content)
                link = f"https://www.binance.com/en/support/announcement/{art['code']}"
                await send_channel("BINANCE", token, title, link, ttime)
                if ttime: schedule_t10("BINANCE", token, ttime)
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
            token, ttime = ai_extract_token_and_time(title, title)
            link = f"https://www.okx.com/help/{item['url']}"
            await send_channel("OKX", token, title, link, ttime)
            if ttime: schedule_t10("OKX", token, ttime)
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
            token, ttime = ai_extract_token_and_time(title, item.get('description',''))
            link = item.get('url','https://announcements.bybit.com')
            await send_channel("BYBIT", token, title, link, ttime)
            if ttime: schedule_t10("BYBIT", token, ttime)
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
            token, ttime = ai_extract_token_and_time(title, title)
            await send_channel("GATE", token, title, f"https://www.gate.io/announcements/article/{art['id']}", ttime)
            if ttime: schedule_t10("GATE", token, ttime)
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
            if "will list" in title.lower() or "listing" in title.lower():
                token, ttime = ai_extract_token_and_time(title, title)
                await send_channel("MEXC", token, title, f"https://www.mexc.com/news/{art.get('id','')}", ttime)
                if ttime: schedule_t10("MEXC", token, ttime)
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
            token, ttime = ai_extract_token_and_time(title, art.get('annDesc',''))
            await send_channel("BITGET", token, title, f"https://www.bitget.com/support/articles/{art.get('id')}", ttime)
            if ttime: schedule_t10("BITGET", token, ttime)
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
            token, ttime = ai_extract_token_and_time(title, art.get('summary',''))
            await send_channel("KUCOIN", token, title, f"https://www.kucoin.com/news/{art.get('id')}", ttime)
            if ttime: schedule_t10("KUCOIN", token, ttime)
            mark_sent(_id)
    except Exception as e: logging.error(f"Kucoin {e}")

async def check_htx():
    try:
        data = safe_json_get("https://www.htx.com/-/x/hbg/v1/announcement/list?limit=5&r=new-listing")
        if not data: return
        for art in data.get('data',{}).get('list',[]):
            _id = f"HTX_{art['id']}"
            if is_sent(_id): continue
            title = art['title']
            token, ttime = ai_extract_token_and_time(title, title)
            await send_channel("HTX", token, title, f"https://www.htx.com/support/{art['id']}", ttime)
            if ttime: schedule_t10("HTX", token, ttime)
            mark_sent(_id)
    except Exception as e: logging.error(f"HTX {e}")

async def check_bingx():
    try:
        data = safe_json_get("https://www.bingx.com/api/cms/v1/notice/list?current=1&size=5&type=1")
        if not data: return
        for art in data.get('data',{}).get('records',[]):
            if "listing" not in art.get('title','').lower(): continue
            _id = f"BINGX_{art['id']}"
            if is_sent(_id): continue
            title = art['title']
            token, ttime = ai_extract_token_and_time(title, title)
            await send_channel("BINGX", token, title, f"https://bingx.com/en-us/support/articles/{art['id']}/", ttime)
            if ttime: schedule_t10("BINGX", token, ttime)
            mark_sent(_id)
    except Exception as e: logging.error(f"BingX {e}")

async def check_lbank():
    try:
        data = safe_json_get("https://www.lbank.com/api/cms/lbank/announcement/list?category=1&page=1&size=5")
        if not data: return
        for art in data.get('data',{}).get('list',[]):
            title = art.get('title','')
            _id = f"LBANK_{art.get('id', title)}"
            if is_sent(_id): continue
            if "will list" in title.lower():
                token, ttime = ai_extract_token_and_time(title, title)
                await send_channel("LBANK", token, title, f"https://www.lbank.com/en-US/announcement/{art.get('id','')}", ttime)
                if ttime: schedule_t10("LBANK", token, ttime)
                mark_sent(_id)
    except Exception as e: logging.error(f"Lbank {e}")

async def check_upbit():
    try:
        data = safe_json_get("https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=5&category=trade")
        if not data: return
        for art in data.get('data',{}).get('list',[]):
            _id = f"UPBIT_{art['id']}"
            if is_sent(_id): continue
            title = art['title']
            if "listing" in title.lower() or "support" in title.lower():
                token, ttime = ai_extract_token_and_time(title, title)
                await send_channel("UPBIT", token, title, f"https://upbit.com/service_center/notice?id={art['id']}", ttime)
                if ttime: schedule_t10("UPBIT", token, ttime)
                mark_sent(_id)
    except Exception as e: logging.error(f"Upbit {e}")

async def main_loop():
    scheduler.start()
    logging.info(f"Bot started for channel {CHAT_ID}")
    try:
        await bot.send_message(chat_id=CHAT_ID, text="✅ <b>Listing Bot đã online (Free Web Service)</b>\nĐang theo dõi 11 sàn: Binance, OKX, MEXC, Bitget, Gate, Upbit, Kucoin, Bybit, HTX, BingX, Lbank", parse_mode='HTML')
    except: pass
    while True:
        try:
            await asyncio.gather(check_binance(), check_okx(), check_bybit(), check_gate(), check_mexc(), check_bitget(), check_kucoin(), check_htx(), check_bingx(), check_lbank(), check_upbit())
        except Exception as e:
            logging.error(f"Loop error {e}")
        await asyncio.sleep(45)

if __name__ == "__main__":
    asyncio.run(main_loop())
