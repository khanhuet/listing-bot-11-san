import os, re, sqlite3, requests, asyncio, logging, json, threading, time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telegram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil import parser as date_parser
from flask import Flask

app = Flask(__name__)
@app.route('/')
def home(): return "Bot V3 Final - Only New Listings"
def run_web():
    try: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
    except: pass
threading.Thread(target=run_web, daemon=True).start()

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = Bot(token=BOT_TOKEN)
scheduler = AsyncIOScheduler()
logging.basicConfig(level=logging.INFO)

# DB chống spam lại
conn = sqlite3.connect('listings.db', check_same_thread=False)
conn.execute('CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY)')
def is_sent(_id): return conn.execute('SELECT 1 FROM sent WHERE id=?', (_id,)).fetchone() is not None
def mark_sent(_id):
    try: conn.execute('INSERT OR IGNORE INTO sent VALUES (?)', (_id,)); conn.commit()
    except: pass

HEADERS = {"User-Agent": "Mozilla/5.0"}

def safe_json_get(url, headers=None, timeout=15):
    try:
        r = requests.get(url, headers=headers or HEADERS, timeout=timeout)
        if r.status_code != 200 or not r.text.strip(): return None
        return r.json()
    except: return None

# --- LẤY THÔNG TIN TOKEN TỪ COINGECKO ---
def get_token_extra(symbol):
    info = {"contract": None, "website": None, "cmc": f"https://coinmarketcap.com/search/?query={symbol}", "cg": f"https://www.coingecko.com/en/search?query={symbol}", "listed_on": []}
    if symbol == "NEW": return info
    try:
        s = safe_json_get(f"https://api.coingecko.com/api/v3/search?query={symbol}")
        if not s or not s.get('coins'): return info
        coin = next((c for c in s['coins'] if c['symbol'].lower() == symbol.lower()), s['coins'][0])
        coin_id = coin['id']
        d = safe_json_get(f"https://api.coingecko.com/api/v3/coins/{coin_id}?localization=false&tickers=true&market_data=false&community_data=false&developer_data=false")
        if d:
            plats = d.get('platforms', {})
            for plat in ['ethereum','bnb-smart-chain','base','arbitrum-one','solana','polygon-pos']:
                if plats.get(plat):
                    info['contract'] = plats[plat]
                    break
            if not info['contract']:
                for v in plats.values():
                    if v: info['contract']=v; break
            links = d.get('links',{})
            if links.get('homepage') and links['homepage'][0]:
                info['website'] = links['homepage'][0]
            tickers = d.get('tickers', [])[:30]
            exchanges = list(set([t['market']['name'] for t in tickers if t.get('market')]))
            info['listed_on'] = exchanges[:8]
            info['cg'] = f"https://www.coingecko.com/en/coins/{coin_id}"
            info['cmc'] = f"https://coinmarketcap.com/currencies/{coin_id}/"
    except: pass
    return info

def parse_symbol(title):
    m = re.search(r'\(([A-Z0-9]{2,12})\)', title)
    if m: return m.group(1)
    m = re.search(r'\$([A-Z]{2,12})\b', title)
    if m: return m.group(1)
    m = re.search(r'\b([A-Z]{2,10})\b', title)
    return m.group(1).upper() if m else "NEW"

def extract_contract(text):
    evm = re.search(r'0x[a-fA-F0-9]{40}', text or "")
    if evm: return evm.group(0)
    return None

def is_recent(publish_ts_ms_or_sec):
    """Chỉ cho phép thông báo trong 60 phút vừa qua - chặn tin cũ"""
    if not publish_ts_ms_or_sec: return True # nếu không có time thì cho qua lần đầu rồi sẽ bị chặn bởi DB
    try:
        # API có thể trả ms hoặc sec hoặc ISO string
        if isinstance(publish_ts_ms_or_sec, (int, float)):
            ts = publish_ts_ms_or_sec / 1000 if publish_ts_ms_or_sec > 1e12 else publish_ts_ms_or_sec
        else:
            dt = date_parser.parse(str(publish_ts_ms_or_sec))
            ts = dt.timestamp()
        age_minutes = (time.time() - ts) / 60
        # CHỈ BÁO TIN MỚI DƯỚI 60 PHÚT - bạn có thể đổi 30 nếu muốn gắt hơn
        return age_minutes <= 60 and age_minutes >= -5
    except: return True

FIRST_RUN_DONE = False

async def send_final(exchange, token, title, link, raw_content="", publish_time=None):
    global FIRST_RUN_DONE
    _id = f"{exchange}_{token}_{link}"
    if is_sent(_id): return
    
    # --- LOGIC CHẶN TIN CŨ ---
    if publish_time is not None:
        if not is_recent(publish_time):
            # Tin cũ trước thời điểm hiện tại -> chỉ đánh dấu đã gửi, không thông báo
            if not FIRST_RUN_DONE:
                logging.info(f"Skip old {exchange} {token} - {publish_time}")
            mark_sent(_id)
            return
    else:
        # Nếu là lần chạy đầu tiên của bot, tất cả tin cũ sẽ bị bỏ qua không báo
        if not FIRST_RUN_DONE:
            mark_sent(_id)
            return

    contract_from_ann = extract_contract(raw_content)
    extra = get_token_extra(token)
    contract = contract_from_ann or extra['contract']
    website = extra['website']
    listed = [x for x in extra['listed_on'] if exchange.lower() not in x.lower()][:5]

    # --- FORMAT CHUẨN NHƯ BẠN YÊU CẦU ---
    lines = []
    lines.append(f"🔔 <b>${token} | {exchange} LISTING</b>")
    lines.append("")
    lines.append(title[:200])
    lines.append("")
    if contract:
        lines.append(f"📜 Contract: <code>{contract[:6]}...{contract[-4:]}</code>")
    if listed:
        lines.append(f"🔄 Other Exchanges: {', '.join(listed)}")
    lines.append("")
    # 3 link xanh ở dưới cùng, không dùng nút
    src_link = link
    web_link = website or f"https://www.google.com/search?q={token}+crypto+official+site"
    cmc_link = extra['cmc']
    lines.append(f"<a href='{src_link}'>Source</a> | <a href='{web_link}'>Website</a> | <a href='{cmc_link}'>CMC / CG</a>")

    msg = "\n".join(lines)
    try:
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML', disable_web_page_preview=True)
        mark_sent(_id)
        logging.info(f"Sent {exchange} {token}")
    except Exception as e:
        logging.error(f"Send fail {e}")

# --- CHECK CÁC SÀN CÓ KÈM THỜI GIAN ---
async def check_binance():
    try:
        data = safe_json_get("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=5&catalogId=48")
        if not data: return
        for art in data.get('data',{}).get('catalogs',[{}])[0].get('articles',[]):
            title = art['title']
            if "will list" not in title.lower() and "to list" not in title.lower(): continue
            token = parse_symbol(title)
            link = f"https://www.binance.com/en/support/announcement/detail/{art['code']}"
            publish = art.get('releaseDate') # ms
            detail = safe_json_get(f"https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode={art['code']}")
            content = json.dumps(detail) if detail else title
            await send_final("BINANCE", token, title, link, content, publish)
    except Exception as e: logging.error(f"Binance {e}")

async def check_okx():
    try:
        data = safe_json_get("https://www.okx.com/api/v5/support/announcement?annType=announcements-new-listings&page=1&pageSize=5")
        if not data: return
        for item in data.get('data',{}).get('announcements',[]):
            title = item['title']
            token = parse_symbol(title)
            link = f"https://www.okx.com{item.get('url','')}" if item.get('url','').startswith('/') else f"https://www.okx.com/help/{item.get('url','')}"
            publish = item.get('pTime') or item.get('publishTime')
            await send_final("OKX", token, title, link, title, publish)
    except Exception as e: logging.error(f"OKX {e}")

async def check_bybit():
    try:
        data = safe_json_get("https://api.bybit.com/v5/announcements?locale=en-US&type=new_crypto_listing&page=1&limit=5")
        if not data: return
        for item in data.get('result',{}).get('list',[]):
            title = item['title']
            token = parse_symbol(title)
            link = item.get('url') or f"https://announcements.bybit.com/en-US/article/{item.get('id','')}"
            publish = item.get('dateTimestamp') or item.get('publishTime')
            await send_final("BYBIT", token, title, link, item.get('description',''), publish)
    except Exception as e: logging.error(f"Bybit {e}")

async def check_gate():
    try:
        data = safe_json_get("https://www.gate.io/apiw/v2/cms/announcement/list?categroy_id=3&lang=en&page=1&page_size=5")
        if not data: return
        for art in data.get('data',{}).get('list',[]):
            title = art['title']
            token = parse_symbol(title)
            link = f"https://www.gate.io/announcements/article/{art['id']}"
            publish = art.get('publish_time') or art.get('create_time')
            await send_final("GATE", token, title, link, title, publish)
    except Exception as e: logging.error(f"Gate {e}")

async def check_mexc():
    try:
        data = safe_json_get("https://www.mexc.com/api/operation/announcement/list?announceType=1&pageNum=1&pageSize=5")
        if not data: return
        lst = data.get('data',{}).get('list',[]) or data.get('data',[])[:5]
        for art in lst:
            title = art.get('title','')
            if "will list" not in title.lower() and "listing" not in title.lower(): continue
            token = parse_symbol(title)
            link = f"https://www.mexc.com/news/{art.get('id','')}"
            publish = art.get('publishTime') or art.get('createTime')
            await send_final("MEXC", token, title, link, title, publish)
    except Exception as e: logging.error(f"MEXC {e}")

async def check_bitget():
    try:
        data = safe_json_get("https://api.bitget.com/api/v2/public/annoucements?annType=new_cryptos&pageSize=5&languageType=0")
        if not data: return
        for art in (data.get('data') or {}).get('announcements',[]):
            if not isinstance(art, dict): continue
            title = art.get('annTitle','')
            token = parse_symbol(title)
            link = f"https://www.bitget.com/support/articles/{art.get('id')}"
            publish = art.get('publishTime')
            await send_final("BITGET", token, title, link, art.get('annDesc',''), publish)
    except Exception as e: logging.error(f"Bitget {e}")

async def check_kucoin():
    try:
        data = safe_json_get("https://www.kucoin.com/_api/cms/articles?category=new-listings&page=1&pageSize=5&lang=en_US")
        if not data: return
        items = data.get('items') or data.get('data',{}).get('items',[])
        for art in items:
            title = art.get('title','')
            token = parse_symbol(title)
            link = f"https://www.kucoin.com/announcement/{art.get('id')}"
            publish = art.get('createdAt') or art.get('publishAt')
            await send_final("KUCOIN", token, title, link, art.get('summary',''), publish)
    except Exception as e: logging.error(f"Kucoin {e}")

async def check_upbit():
    try:
        data = safe_json_get("https://api-manager.upbit.com/api/v1/announcements?os=web&page=1&per_page=5&category=trade")
        if not data: return
        for art in data.get('data',{}).get('list',[]):
            title = art['title']
            if "listing" not in title.lower() and "support" not in title.lower(): continue
            token = parse_symbol(title)
            link = f"https://upbit.com/service_center/notice?id={art['id']}"
            publish = art.get('created_at') or art.get('listed_at')
            await send_final("UPBIT", token, title, link, title, publish)
    except Exception as e: logging.error(f"Upbit {e}")

async def main_loop():
    global FIRST_RUN_DONE
    scheduler.start()
    # Lần đầu chạy: quét hết và đánh dấu đã gửi để không spam tin cũ
    logging.info("First run - marking old listings as sent...")
    try:
        await asyncio.gather(check_binance(), check_okx(), check_bybit(), check_gate(), check_mexc(), check_bitget(), check_kucoin(), check_upbit())
    except: pass
    FIRST_RUN_DONE = True
    logging.info("First run done. Bot will only notify NEW listings from now.")
    try: await bot.send_message(chat_id=CHAT_ID, text="✅ <b>Bot V3 Final đã online</b>\nChỉ báo tin mới trong 60 phút - Format gọn như yêu cầu", parse_mode='HTML')
    except: pass
    while True:
        try:
            await asyncio.gather(check_binance(), check_okx(), check_bybit(), check_gate(), check_mexc(), check_bitget(), check_kucoin(), check_upbit())
        except Exception as e: logging.error(f"Loop {e}")
        await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main_loop())
