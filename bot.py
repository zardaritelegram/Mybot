

import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ╔══════════════════════════════════════════════════════════════╗
# ║                    IMPORTS & CONFIG                          ║
# ╚══════════════════════════════════════════════════════════════╝
import httpx
import sqlite3
import asyncio
import io
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

TOKEN = os.environ.get("TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
GOLD_API_KEY = "goldapi-d23da414dfdcbbe06a2e2ce8d28a095c-io"


# ╔══════════════════════════════════════════════════════════════╗
# ║                       DATABASE                               ║
# ║  توابع دیتابیس - دست نزن                                    ║
# ╚══════════════════════════════════════════════════════════════╝
def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
        status TEXT DEFAULT 'pending', approved_at TEXT,
        expires_at TEXT, daily_msg INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS watchlist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, symbol TEXT, market TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS alarms (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        symbol TEXT, market TEXT, target REAL, direction TEXT,
        active INTEGER DEFAULT 1, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_state (
        user_id INTEGER PRIMARY KEY, state TEXT, data TEXT)''')
    try:
        c.execute("ALTER TABLE users ADD COLUMN daily_msg INTEGER DEFAULT 1")
    except:
        pass
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def save_user(user_id, username, first_name):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, status, daily_msg) VALUES (?, ?, ?, 'pending', 1)",
              (user_id, username, first_name))
    conn.commit()
    conn.close()

def approve_user(user_id):
    now = datetime.now()
    expires = now + timedelta(days=365)
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE users SET status='active', approved_at=?, expires_at=?, daily_msg=1 WHERE user_id=?",
              (now.strftime("%Y-%m-%d %H:%M"), expires.strftime("%Y-%m-%d %H:%M"), user_id))
    conn.commit()
    conn.close()

def reject_user(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE users SET status='rejected' WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def set_state(user_id, state, data=""):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO user_state (user_id, state, data) VALUES (?, ?, ?)", (user_id, state, data))
    conn.commit()
    conn.close()

def get_state(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT state, data FROM user_state WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row if row else (None, None)

def clear_state(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("DELETE FROM user_state WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def get_watchlist(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT id, symbol, market FROM watchlist WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def add_watchlist(user_id, symbol, market):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT id FROM watchlist WHERE user_id=? AND symbol=?", (user_id, symbol))
    if c.fetchone():
        conn.close()
        return False
    c.execute("INSERT INTO watchlist (user_id, symbol, market) VALUES (?, ?, ?)", (user_id, symbol, market))
    conn.commit()
    conn.close()
    return True

def remove_watchlist(wid):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("DELETE FROM watchlist WHERE id=?", (wid,))
    conn.commit()
    conn.close()

def get_alarms(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT id, symbol, market, target, direction FROM alarms WHERE user_id=? AND active=1", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def add_alarm(user_id, symbol, market, target, direction):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO alarms (user_id, symbol, market, target, direction, active, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
              (user_id, symbol, market, target, direction, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def deactivate_alarm(alarm_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE alarms SET active=0 WHERE id=?", (alarm_id,))
    conn.commit()
    conn.close()

def remove_alarm(alarm_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("DELETE FROM alarms WHERE id=?", (alarm_id,))
    conn.commit()
    conn.close()

def get_all_active_alarms():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT id, user_id, symbol, market, target, direction FROM alarms WHERE active=1")
    rows = c.fetchall()
    conn.close()
    return rows


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: FINANCIAL                           ║
# ║                                                              ║
# ║  برای حذف کامل این ماژول:                                   ║
# ║  ۱. این بلوک رو پاک کن (از اینجا تا خط ═══ بعدی)          ║
# ║  ۲. در MAIN MENU خط financial رو کامنت کن                  ║
# ║  ۳. در ROUTER خط financial رو کامنت کن                     ║
# ╚══════════════════════════════════════════════════════════════╝

IRAN_SYMBOLS = {
    "USD": "price_dollar_rl", "EUR": "price_eur", "GBP": "price_gbp",
    "GOLD18": "geram18", "COIN": "sekee", "HALFCOIN": "nim",
    "QTRCOIN": "rob", "SILVER": "silver_999", "MITHGAL": "mesghal",
}
CRYPTO_SYMBOLS = {
    "BTC": "bitcoin", "ETH": "ethereum", "USDT": "tether",
    "BNB": "binancecoin", "TRX": "tron", "SOL": "solana",
    "ADA": "cardano", "XRP": "ripple", "DOGE": "dogecoin",
    "DOT": "polkadot", "MATIC": "matic-network", "LTC": "litecoin",
    "AVAX": "avalanche-2", "LINK": "chainlink", "UNI": "uniswap",
}
GOLDAPI_SYMBOLS = {
    "GOLD": ("XAU", "USD"), "XAUUSD": ("XAU", "USD"),
    "SILVER": ("XAG", "USD"), "XAGUSD": ("XAG", "USD"),
}
FOREX_YAHOO = {
    "EURUSD": "EURUSD%3DX", "GBPUSD": "GBPUSD%3DX",
    "USDCAD": "USDCAD%3DX", "USDJPY": "USDJPY%3DX",
    "AUDUSD": "AUDUSD%3DX", "USDCHF": "USDCHF%3DX",
    "NZDUSD": "NZDUSD%3DX", "EURGBP": "EURGBP%3DX",
    "EURJPY": "EURJPY%3DX", "GBPJPY": "GBPJPY%3DX",
    "OIL": "BZ%3DF", "BRENT": "BZ%3DF", "WTI": "CL%3DF",
}

def detect_market(symbol):
    s = symbol.upper()
    if s in IRAN_SYMBOLS: return "iran"
    if s in CRYPTO_SYMBOLS: return "crypto"
    if s in GOLDAPI_SYMBOLS: return "goldapi"
    return "forex"

def fmt_price(price, unit):
    if price is None: return "---"
    if unit == "IRR": return f"{price:,}"
    if price < 1000: return f"{price:.5f}"
    return f"{price:,.2f}"

async def get_goldapi_price(metal="XAU", currency="USD"):
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://www.goldapi.io/api/{metal}/{currency}",
                headers={"x-access-token": GOLD_API_KEY})
            data = r.json()
            price = data.get("price")
            change = data.get("chp", 0)
            return price, change, "🟢" if change >= 0 else "🔴"
    except Exception as e:
        print(f"GoldAPI error: {e}")
        return None, 0, "⚪"

async def get_yahoo_price(symbol):
    try:
        async with httpx.AsyncClient(timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d")
            meta = r.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice", 0)
            prev = meta.get("chartPreviousClose", meta.get("previousClose", price))
            change = ((price - prev) / prev * 100) if prev else 0
            return price, change, "🟢" if change >= 0 else "🔴"
    except:
        return None, 0, "⚪"

async def get_price_by_symbol(symbol, market):
    symbol = symbol.upper()
    try:
        async with httpx.AsyncClient(timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}) as client:
            if market == "iran":
                key = IRAN_SYMBOLS.get(symbol)
                if not key: return None, "نامشخص"
                r = await client.get("https://call4.tgju.org/ajax.json")
                val = r.json().get("current", {}).get(key, {}).get("p", "").replace(",", "")
                return int(float(val)), "IRR"
            elif market == "crypto":
                cg_id = CRYPTO_SYMBOLS.get(symbol)
                if not cg_id: return None, "نامشخص"
                r = await client.get("https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": cg_id, "vs_currencies": "usd"})
                return r.json().get(cg_id, {}).get("usd"), "USD"
            elif market == "goldapi":
                metal, currency = GOLDAPI_SYMBOLS.get(symbol, ("XAU", "USD"))
                r = await client.get(f"https://www.goldapi.io/api/{metal}/{currency}",
                    headers={"x-access-token": GOLD_API_KEY})
                return r.json().get("price"), "USD"
            else:
                yahoo_sym = FOREX_YAHOO.get(symbol, symbol + "%3DX")
                r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?interval=1m&range=1d")
                return r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice"), "USD"
    except:
        return None, "نامشخص"

async def validate_symbol(symbol):
    market = detect_market(symbol.upper())
    price, unit = await get_price_by_symbol(symbol, market)
    if price and price > 0:
        return True, market, price, unit
    return False, None, None, None

async def get_iran_prices():
    try:
        async with httpx.AsyncClient(timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}) as client:
            current = (await client.get("https://call4.tgju.org/ajax.json")).json().get("current", {})
            def gp(key):
                try: return int(float(current[key]["p"].replace(",", "")))
                except: return None
            return {"dollar": gp("price_dollar_rl"), "gold18": gp("geram18"),
                    "gold_coin": gp("sekee"), "half_coin": gp("nim"),
                    "quarter": gp("rob"), "gram_gold": gp("mesghal"), "silver": gp("silver_999")}
    except Exception as e:
        print(f"Iran prices error: {e}")
        return None

async def get_crypto_prices():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            data = (await client.get("https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "bitcoin,ethereum,tether,binancecoin,tron",
                        "vs_currencies": "usd", "include_24hr_change": "true"})).json()
            def fmt(cid):
                c = data.get(cid, {})
                p, ch = c.get("usd", 0), c.get("usd_24h_change", 0)
                return p, ch, "🟢" if ch >= 0 else "🔴"
            return {"bitcoin": fmt("bitcoin"), "ethereum": fmt("ethereum"),
                    "tether": fmt("tether"), "bnb": fmt("binancecoin"), "tron": fmt("tron")}
    except Exception as e:
        print(f"Crypto error: {e}")
        return None

async def get_forex_prices():
    try:
        gp, gc, ga = await get_goldapi_price("XAU", "USD")
        sp, sc, sa = await get_goldapi_price("XAG", "USD")
        return {"gold": (gp, gc, ga), "silver": (sp, sc, sa),
                "oil": await get_yahoo_price("BZ%3DF"),
                "eur": await get_yahoo_price("EURUSD%3DX"),
                "gbp": await get_yahoo_price("GBPUSD%3DX")}
    except Exception as e:
        print(f"Forex error: {e}")
        return None

async def check_alarms(app):
    for alarm_id, user_id, symbol, market, target, direction in get_all_active_alarms():
        try:
            price, unit = await get_price_by_symbol(symbol, market)
            if price is None: continue
            if (direction == "above" and price >= target) or (direction == "below" and price <= target):
                arrow = "📈" if direction == "above" else "📉"
                await app.bot.send_message(user_id,
                    f"🔔 آلارم فعال شد!\n\n{arrow} {symbol}\n"
                    f"قیمت هدف: {fmt_price(target, unit)}\n"
                    f"قیمت فعلی: {fmt_price(price, unit)} {unit}\n"
                    f"🕐 {datetime.now().strftime('%H:%M:%S')}")
                deactivate_alarm(alarm_id)
        except Exception as e:
            print(f"Alarm error {alarm_id}: {e}")

async def alarm_loop(app):
    while True:
        await check_alarms(app)
        await asyncio.sleep(60)

def financial_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 بازار ایران", callback_data="fin_currency")],
        [InlineKeyboardButton("₿ بازار کریپتو", callback_data="fin_crypto")],
        [InlineKeyboardButton("📈 بازار فارکس", callback_data="fin_forex")],
        [InlineKeyboardButton("👁 واچ لیست شخصی", callback_data="fin_watchlist")],
        [InlineKeyboardButton("🔔 آلارم قیمت", callback_data="fin_alarm")],
        [InlineKeyboardButton("📐 میزان خرید", callback_data="ps_size")],  # ← حذف = پاک کن این خط
        [InlineKeyboardButton("📊 بک‌تست", callback_data="fin_backtest")],  # ← حذف = پاک کن این خط
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

def watchlist_menu(user_id):
    items = get_watchlist(user_id)
    keyboard = []
    
    # دکمه‌های حذف - 3تایی در هر سطر
    row = []
    for wid, symbol, market in items:
        row.append(InlineKeyboardButton(f"❌ {symbol}", callback_data=f"wl_del_{wid}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("➕ افزودن ارز", callback_data="wl_add")])
    keyboard.append([InlineKeyboardButton("📊 مشاهده قیمت‌ها", callback_data="wl_view")])
    keyboard.append([InlineKeyboardButton("🔙 برگشت", callback_data="back_financial")])
    return InlineKeyboardMarkup(keyboard)

def alarm_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ آلارم جدید", callback_data="alarm_new")],
        [InlineKeyboardButton("📋 آلارم‌های فعال", callback_data="alarm_list")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_financial")],
    ])

async def handle_financial(query, user_id):
    data = query.data

    if data == "menu_financial":
        await query.edit_message_text("💰 بازارهای مالی:", reply_markup=financial_menu())

    elif data == "back_financial":
        clear_state(user_id)
        await query.edit_message_text("💰 بازارهای مالی:", reply_markup=financial_menu())

    elif data == "fin_currency":
        await query.edit_message_text("⏳ در حال دریافت قیمت‌ها...")
        prices = await get_iran_prices()
        if prices:
            def f(v): return f"{v:,}" if v else "---"
            text = ("💵 Iran Market\n`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
                f"`{'USD':<10} {f(prices['dollar']):>15} IRR`\n"
                f"`{'Gold 18':<10} {f(prices['gold18']):>15} IRR`\n"
                f"`{'Coin':<10} {f(prices['gold_coin']):>15} IRR`\n"
                f"`{'Half Coin':<10} {f(prices['half_coin']):>15} IRR`\n"
                f"`{'Qtr Coin':<10} {f(prices['quarter']):>15} IRR`\n"
                f"`{'Mithgal':<10} {f(prices['gram_gold']):>15} IRR`\n"
                f"`{'Silver':<10} {f(prices['silver']):>15} IRR`\n"
                f"`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`\n🕐 {datetime.now().strftime('%H:%M:%S')}")
        else:
            text = "❌ خطا در دریافت قیمت‌ها."
        await query.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back_financial")]]))

    elif data == "fin_crypto":
        await query.edit_message_text("⏳ در حال دریافت قیمت‌ها...")
        prices = await get_crypto_prices()
        if prices:
            def row(n, d):
                p, ch, ar = d
                return f"`{n:<5} ${p:>10,.2f}  {ar} {ch:>+6.2f}%`"
            text = ("₿ Crypto Market\n`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
                + row("BTC", prices["bitcoin"]) + "\n"
                + row("ETH", prices["ethereum"]) + "\n"
                + row("USDT", prices["tether"]) + "\n"
                + row("BNB", prices["bnb"]) + "\n"
                + row("TRX", prices["tron"]) + "\n"
                f"`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`\n🕐 {datetime.now().strftime('%H:%M:%S')}")
        else:
            text = "❌ خطا در دریافت قیمت‌ها."
        await query.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back_financial")]]))

    elif data == "fin_forex":
        await query.edit_message_text("⏳ در حال دریافت قیمت‌ها...")
        prices = await get_forex_prices()
        if prices:
            def row(n, d):
                p, ch, ar = d
                if p is None: return f"`{n:<8} {'---':>12}`"
                return f"`{n:<8} {fmt_price(p, 'USD'):>12}  {ar} {ch:>+6.2f}%`"
            text = ("📈 Forex Market\n`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
                + row("GOLD", prices["gold"]) + "\n"
                + row("SILVER", prices["silver"]) + "\n"
                + row("OIL", prices["oil"]) + "\n"
                + row("EUR/USD", prices["eur"]) + "\n"
                + row("GBP/USD", prices["gbp"]) + "\n"
                f"`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`\n🕐 {datetime.now().strftime('%H:%M:%S')}")
        else:
            text = "❌ خطا در دریافت قیمت‌ها."
        await query.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="back_financial")]]))

    elif data == "fin_watchlist":
        await query.edit_message_text("👁 واچ لیست شخصی:", reply_markup=watchlist_menu(user_id))

    elif data == "wl_add":
        set_state(user_id, "wl_add")
        await query.edit_message_text(
            "➕ نماد ارز را تایپ کنید:\n\nمثال:\n`BTC` `GOLD` `USDCAD` `EURUSD`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="fin_watchlist")]]))

    elif data == "wl_view":
        items = get_watchlist(user_id)
        if not items:
            await query.answer("واچ لیست خالی است!", show_alert=True)
            return
        await query.edit_message_text("⏳ در حال دریافت قیمت‌ها...")
        lines = []
        for _, symbol, market in items:
            price, unit = await get_price_by_symbol(symbol, market)
            lines.append(f"`{symbol:<10} {fmt_price(price, unit):>15} {unit}`")
        text = ("👁 واچ لیست من\n`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
            + "\n".join(lines) + f"\n`━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`\n🕐 {datetime.now().strftime('%H:%M:%S')}")
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=watchlist_menu(user_id))

    elif data.startswith("wl_del_"):
        remove_watchlist(int(data.replace("wl_del_", "")))
        await query.edit_message_text("✅ حذف شد.", reply_markup=watchlist_menu(user_id))

    elif data == "fin_alarm":
        await query.edit_message_text("🔔 آلارم قیمت:", reply_markup=alarm_menu())

    elif data == "alarm_new":
        set_state(user_id, "alarm_symbol")
        await query.edit_message_text(
            "🔔 نماد ارز را وارد کنید:\n\nمثال:\n`BTC` `GOLD` `USDCAD`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="fin_alarm")]]))

    elif data == "alarm_list":
        alarms = get_alarms(user_id)
        if not alarms:
            await query.answer("هیچ آلارم فعالی ندارید!", show_alert=True)
            return
        text = "📋 آلارم‌های فعال:\n\n"
        keyboard = []
        for aid, symbol, market, target, direction in alarms:
            unit = "IRR" if market == "iran" else "USD"
            arrow = "📈" if direction == "above" else "📉"
            text += f"{arrow} {symbol} ← {fmt_price(target, unit)} {unit}\n"
            keyboard.append([InlineKeyboardButton(f"🗑 حذف {symbol}", callback_data=f"alarm_del_{aid}")])
        keyboard.append([InlineKeyboardButton("🔙 برگشت", callback_data="fin_alarm")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("alarm_del_"):
        remove_alarm(int(data.replace("alarm_del_", "")))
        await query.edit_message_text("✅ آلارم حذف شد.", reply_markup=alarm_menu())

    elif data in ["alarm_dir_above", "alarm_dir_below"]:
        state, sdata = get_state(user_id)
        if state == "alarm_direction" and sdata:
            parts = sdata.split("|")
            symbol, market, target, unit = parts[0], parts[1], float(parts[2]), parts[3]
            direction = "above" if data == "alarm_dir_above" else "below"
            add_alarm(user_id, symbol, market, target, direction)
            clear_state(user_id)
            arrow = "📈" if direction == "above" else "📉"
            await query.edit_message_text(
                f"✅ آلارم ثبت شد!\n\n{arrow} {symbol}\nقیمت هدف: {fmt_price(target, unit)} {unit}",
                reply_markup=alarm_menu())

async def handle_financial_message(user_id, text, update):
    state, data = get_state(user_id)

    if state == "wl_add":
        symbol = text.upper()
        await update.message.reply_text("⏳ در حال بررسی...")
        valid, market, price, unit = await validate_symbol(symbol)
        if valid:
            if add_watchlist(user_id, symbol, market):
                await update.message.reply_text(
                    f"✅ {symbol} اضافه شد!\n💰 قیمت: {fmt_price(price, unit)} {unit}",
                    reply_markup=watchlist_menu(user_id))
            else:
                await update.message.reply_text(f"⚠️ {symbol} قبلاً هست.", reply_markup=watchlist_menu(user_id))
        else:
            await update.message.reply_text(f"❌ '{symbol}' پیدا نشد.", reply_markup=watchlist_menu(user_id))
        clear_state(user_id)
        return True

    elif state == "alarm_symbol":
        symbol = text.upper()
        await update.message.reply_text("⏳ در حال بررسی...")
        valid, market, price, unit = await validate_symbol(symbol)
        if valid:
            set_state(user_id, "alarm_price", f"{symbol}|{market}|{price}|{unit}")
            await update.message.reply_text(
                f"✅ {symbol} پیدا شد!\n💰 قیمت: {fmt_price(price, unit)} {unit}\n\nقیمت هدف را وارد کنید:")
        else:
            await update.message.reply_text(f"❌ '{symbol}' پیدا نشد. دوباره امتحان کنید:")
        return True

    elif state == "alarm_price":
        try:
            target = float(text.replace(",", ""))
            parts = data.split("|")
            symbol, market, unit = parts[0], parts[1], parts[3]
            set_state(user_id, "alarm_direction", f"{symbol}|{market}|{target}|{unit}")
            await update.message.reply_text("جهت آلارم:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"📈 بالاتر از {fmt_price(target, unit)}", callback_data="alarm_dir_above")],
                    [InlineKeyboardButton(f"📉 پایین‌تر از {fmt_price(target, unit)}", callback_data="alarm_dir_below")],
                ]))
        except:
            await update.message.reply_text("❌ عدد وارد کنید:")
        return True

    return False

# ═══════════════ پایان MODULE: FINANCIAL ═══════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: ACCOUNTING                          ║
# ║  برای فعال کردن: کد رو اینجا اضافه کن                      ║
# ║  برای حذف: این بلوک رو پاک کن                               ║
# ╚══════════════════════════════════════════════════════════════╝

def accounting_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ثبت درآمد", callback_data="acc_income")],
        [InlineKeyboardButton("➖ ثبت هزینه", callback_data="acc_expense")],
        [InlineKeyboardButton("📋 لیست تراکنش‌ها", callback_data="acc_list")],
        [InlineKeyboardButton("📈 گزارش ماهانه", callback_data="acc_report")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

async def handle_accounting(query, user_id):
    data = query.data
    if data == "menu_accounting":
        await query.edit_message_text("📊 حسابداری شخصی:", reply_markup=accounting_menu())
    else:
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

# ═══════════════ پایان MODULE: ACCOUNTING ══════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: REMINDER                            ║
# ╚══════════════════════════════════════════════════════════════╝

def reminder_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ تسک جدید", callback_data="rem_new")],
        [InlineKeyboardButton("📋 لیست تسک‌ها", callback_data="rem_list")],
        [InlineKeyboardButton("✅ انجام شده‌ها", callback_data="rem_done")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

async def handle_reminder(query, user_id):
    data = query.data
    if data == "menu_reminder":
        await query.edit_message_text("⏰ یادآور و تسک:", reply_markup=reminder_menu())
    else:
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

# ═══════════════ پایان MODULE: REMINDER ════════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: AI CHAT                             ║
# ║  چت با چند منبع هوش مصنوعی (Groq اصلی، Gemini در صورت        ║
# ║  داشتن کلید به‌عنوان پشتیبان) + محدودیت روزانه ویرایش عکس    ║
# ║  برای حذف: این بلوک رو پاک کن                                ║
# ║  + خط menu_ai رو از main_menu پاک کن                        ║
# ║  + خط ai_ رو از ROUTER پاک کن                                ║
# ║  + state های ai_ رو از message_handler پاک کن               ║
# ╚══════════════════════════════════════════════════════════════╝

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")  # اختیاری - وقتی گرفتی همینجا اضافه می‌شود

GROQ_MODEL = "openai/gpt-oss-120b"
GEMINI_MODEL = "gemini-2.0-flash"

IMAGE_EDIT_DAILY_LIMIT = 4

# ─── دیتابیس این ماژول ──────────────────────────────────────
def init_ai_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS ai_image_usage (
        user_id    INTEGER PRIMARY KEY,
        used_count INTEGER DEFAULT 0,
        reset_date TEXT
    )''')
    conn.commit()
    conn.close()

def get_iran_today_str():
    """تاریخ امروز به وقت ایران (برای تشخیص ریست نیمه‌شب ایران)"""
    from datetime import timezone
    iran_now = datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)
    return iran_now.strftime("%Y-%m-%d")

def get_image_usage(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT used_count, reset_date FROM ai_image_usage WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    today = get_iran_today_str()
    if not row or row[1] != today:
        return 0, today
    return row[0], row[1]

def increment_image_usage(user_id):
    used, today = get_image_usage(user_id)
    used += 1
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''INSERT INTO ai_image_usage (user_id, used_count, reset_date) VALUES (?, ?, ?)
                 ON CONFLICT(user_id) DO UPDATE SET used_count=excluded.used_count, reset_date=excluded.reset_date''',
              (user_id, used, today))
    conn.commit()
    conn.close()
    return used

# ─── فراخوانی Groq (منبع اصلی چت) ───────────────────────────
async def call_groq_chat(messages):
    if not GROQ_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                json={"model": GROQ_MODEL, "messages": messages, "temperature": 0.7}
            )
            data = r.json()
            if "choices" in data:
                return data["choices"][0]["message"]["content"]
            print(f"Groq error response: {data}")
            return None
    except Exception as e:
        print(f"خطای Groq: {e}")
        return None

# ─── فراخوانی Gemini (منبع پشتیبان، فعال فقط در صورت داشتن کلید) ─
async def call_gemini_chat(messages):
    if not GEMINI_API_KEY:
        return None
    try:
        # تبدیل فرمت پیام‌های OpenAI-style به فرمت Gemini
        contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
                params={"key": GEMINI_API_KEY},
                json={"contents": contents}
            )
            data = r.json()
            candidates = data.get("candidates", [])
            if candidates:
                return candidates[0]["content"]["parts"][0]["text"]
            print(f"Gemini error response: {data}")
            return None
    except Exception as e:
        print(f"خطای Gemini: {e}")
        return None

async def get_ai_response(messages):
    """تلاش با Groq، در صورت خطا تلاش با Gemini (اگر کلیدش موجود باشد)"""
    response = await call_groq_chat(messages)
    if response:
        return response
    response = await call_gemini_chat(messages)
    if response:
        return response
    return None

# ─── منوها ────────────────────────────────────────────────────
def ai_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 شروع چت", callback_data="ai_start")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

# ─── هندلر دکمه‌ها ────────────────────────────────────────────
async def handle_ai(query, user_id):
    data = query.data
    if data == "menu_ai":
        await query.edit_message_text(
            "🤖 دستیار هوش مصنوعی\n\nهر سوالی داری بپرس، بدون محدودیت تعداد.",
            reply_markup=ai_menu()
        )
    elif data == "ai_start":
        set_state(user_id, "ai_chatting")
        await query.edit_message_text(
            "💬 چت با دستیار هوش مصنوعی\n\nسوال خود را بنویسید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 پایان چت", callback_data="menu_ai")]])
        )

async def handle_ai_message(user_id, text, update):
    state, _ = get_state(user_id)
    if state != "ai_chatting":
        return False

    await update.message.reply_text("⏳ در حال فکر کردن...")
    messages = [
        {"role": "system", "content": "تو یک دستیار هوشمند و مفید هستی که به زبان فارسی پاسخ می‌دهی."},
        {"role": "user", "content": text}
    ]
    response = await get_ai_response(messages)

    if response:
        await update.message.reply_text(
            response,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 پایان چت", callback_data="menu_ai")]])
        )
    else:
        await update.message.reply_text(
            "❌ متأسفانه دستیار هوش مصنوعی موقتاً در دسترس نیست. کمی بعد دوباره امتحان کنید.",
            reply_markup=ai_menu()
        )
    return True

# ═══════════════ پایان MODULE: AI CHAT ═════════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: KIDS                                ║
# ║  قصه‌گویی هوشمند متناسب با سن و جنسیت کودک + خروجی PDF       ║
# ║  از همان منبع AI ماژول قبلی (Groq/Gemini) استفاده می‌کند      ║
# ║  برای حذف: این بلوک رو پاک کن                                ║
# ║  + خط menu_kids رو از main_menu پاک کن                      ║
# ║  + خط kids_ رو از ROUTER پاک کن                              ║
# ║  + state های kids_ رو از message_handler پاک کن             ║
# ╚══════════════════════════════════════════════════════════════╝

def kids_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 قصه جدید", callback_data="kids_story")],
        [InlineKeyboardButton("✏️ تکالیف و مطالعه", callback_data="kids_homework")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

def kids_gender_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👧 دختر", callback_data="kids_gender_girl")],
        [InlineKeyboardButton("👦 پسر", callback_data="kids_gender_boy")],
    ])

def build_story_prompt(age, gender, topic):
    gender_fa = "دختر" if gender == "girl" else "پسر"
    return (
        f"یک داستان کودکانه به زبان فارسی برای یک {gender_fa} {age} ساله بنویس.\n\n"
        f"موضوع داستان: {topic}\n\n"
        f"قوانین مهم:\n"
        f"- داستان باید کاملاً متناسب با سن {age} سال باشد (واژگان ساده و قابل‌فهم برای این سن)\n"
        f"- داستان باید آموزنده باشد و یک نتیجه‌ی اخلاقی ساده و مثبت در پایان داشته باشد\n"
        f"- شخصیت اصلی باید {gender_fa} باشد تا کودک بهتر با آن ارتباط بگیرد\n"
        f"- طول داستان متناسب با سن انتخاب شود: برای سنین کوچک‌تر (۲ تا ۵ سال) کوتاه‌تر "
        f"(در حد ۲ تا ۳ صفحه، حدود ۵۰۰ تا ۸۰۰ کلمه) و برای سنین بزرگ‌تر (۶ تا ۱۴ سال) "
        f"بلندتر و با جزئیات بیشتر (تا ۷ یا ۸ صفحه، حدود ۱۸۰۰ تا ۲۵۰۰ کلمه)؛ در هر حالت "
        f"طول نهایی باید بین ۲ صفحه و ۸ صفحه بماند، نه کمتر و نه بیشتر\n"
        f"- فقط متن داستان را بنویس، بدون مقدمه یا توضیح اضافه\n"
        f"- در پایان، یک خط با عنوان «🌟 پیام داستان:» و نتیجه‌ی اخلاقی کوتاه اضافه کن"
    )

# ─── ساخت PDF فارسی برای قصه ──────────────────────────────────
def build_story_pdf(title, story_text):
    """
    ساخت PDF فارسی با fpdf2. این تابع نیاز به فونت فارسی (Vazirmatn) دارد
    که باید در مسیر fonts/Vazirmatn-Regular.ttf کنار bot.py وجود داشته باشد.
    اگر فونت پیدا نشود، خطا چاپ می‌شود و تابع None برمی‌گرداند (یعنی فقط
    متن در چت نمایش داده می‌شود، بدون فایل PDF).
    """
    try:
        from fpdf import FPDF
        font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Vazirmatn-Regular.ttf")
        if not os.path.exists(font_path):
            print(f"⚠️ فونت فارسی پیدا نشد در مسیر: {font_path}")
            return None

        pdf = FPDF()
        pdf.add_page()
        pdf.add_font("Vazirmatn", "", font_path, uni=True)
        pdf.set_font("Vazirmatn", size=16)
        pdf.set_text_shaping(True)  # ضروری برای رندر درست راست‌به‌چپ فارسی
        pdf.multi_cell(0, 12, title, align="C")
        pdf.ln(5)
        pdf.set_font("Vazirmatn", size=13)
        pdf.multi_cell(0, 10, story_text, align="R")

        buffer = io.BytesIO()
        pdf.output(buffer)
        buffer.seek(0)
        return buffer
    except Exception as e:
        print(f"خطای ساخت PDF قصه: {e}")
        return None

# ─── هندلر دکمه‌ها ────────────────────────────────────────────
async def handle_kids(query, user_id):
    data = query.data

    if data == "menu_kids":
        await query.edit_message_text("👶 بخش فرزندان:", reply_markup=kids_menu())

    elif data == "kids_story":
        set_state(user_id, "kids_age")
        await query.edit_message_text(
            "📖 ساخت قصه جدید\n\nسن کودک را وارد کنید (عدد):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="menu_kids")]])
        )

    elif data in ["kids_gender_girl", "kids_gender_boy"]:
        gender = "girl" if data == "kids_gender_girl" else "boy"
        state, sdata = get_state(user_id)
        if state == "kids_gender":
            set_state(user_id, "kids_topic", f"{sdata}|{gender}")
            await query.edit_message_text(
                "✏️ موضوع داستان را وارد کنید:\n\n"
                "مثال: دوستی، شجاعت، صداقت، حیوانات، فضا، احترام به بزرگ‌ترها"
            )

    elif data == "kids_homework":
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

async def handle_kids_message(user_id, text, update):
    state, data = get_state(user_id)
    text = text.strip()

    if state == "kids_age":
        try:
            age = int(text)
            if age < 2 or age > 14:
                await update.message.reply_text("❌ لطفاً سنی بین ۲ تا ۱۴ سال وارد کنید:")
                return True
            set_state(user_id, "kids_gender", str(age))
            await update.message.reply_text("جنسیت کودک را انتخاب کنید:", reply_markup=kids_gender_menu())
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    elif state == "kids_topic":
        topic = text
        parts = data.split("|")
        age, gender = parts[0], parts[1]

        await update.message.reply_text("⏳ در حال نوشتن قصه... (ممکن است کمی طول بکشد)")

        prompt = build_story_prompt(age, gender, topic)
        messages = [
            {"role": "system", "content": "تو یک نویسنده‌ی خلاق داستان‌های کودکانه به زبان فارسی هستی."},
            {"role": "user", "content": prompt}
        ]
        story = await get_ai_response(messages)

        if not story:
            await update.message.reply_text(
                "❌ متأسفانه ساخت قصه موقتاً ممکن نیست. کمی بعد دوباره امتحان کنید.",
                reply_markup=kids_menu()
            )
            clear_state(user_id)
            return True

        title = f"قصه‌ای درباره {topic}"
        full_text = f"📖 {title}\n\n{story}"

        # تلگرام هر پیام را حداکثر تا ۴۰۹۶ کاراکتر می‌پذیرد؛ قصه‌های بلندتر تکه‌تکه ارسال می‌شوند
        TELEGRAM_LIMIT = 4000
        if len(full_text) <= TELEGRAM_LIMIT:
            await update.message.reply_text(full_text, reply_markup=kids_menu())
        else:
            chunks = [full_text[i:i + TELEGRAM_LIMIT] for i in range(0, len(full_text), TELEGRAM_LIMIT)]
            for i, chunk in enumerate(chunks):
                is_last = (i == len(chunks) - 1)
                await update.message.reply_text(chunk, reply_markup=kids_menu() if is_last else None)

        pdf_buffer = build_story_pdf(title, story)
        if pdf_buffer:
            await update.message.reply_document(
                document=pdf_buffer,
                filename=f"{title}.pdf",
                caption="📥 نسخه PDF قصه برای نگهداری یا چاپ"
            )

        clear_state(user_id)
        return True

    return False

# ═══════════════ پایان MODULE: KIDS ════════════════════════════




# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: FUN (سرگرمی)                        ║
# ║  این ماژول شامل سه زیربخش مستقل است:                        ║
# ║    - سلامت و ورزش (منتقل‌شده از منوی اصلی)                  ║
# ║    - کتاب‌خوانی                                              ║
# ║    - موزیک (جستجو در کانال + منابع رایگان قانونی)           ║
# ║  برای حذف کامل: این بلوک رو پاک کن                          ║
# ║  + خط menu_fun رو از main_menu پاک کن                       ║
# ║  + خط fun_/health_/book_/music_ رو از ROUTER پاک کن         ║
# ║  + state های fun_/book_/music_ رو از message_handler پاک کن║
# ╚══════════════════════════════════════════════════════════════╝

MUSIC_CHANNEL_USERNAME = "LiteMusics"  # بدون @ و بدون t.me/

# ─── دیتابیس این ماژول ──────────────────────────────────────
def init_fun_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS book_tracking (
        user_id     INTEGER PRIMARY KEY,
        title       TEXT,
        current_page INTEGER DEFAULT 0,
        total_pages  INTEGER,
        updated_at   TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS music_index (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        artist      TEXT,
        title       TEXT,
        file_id     TEXT,
        message_id  INTEGER,
        added_at    TEXT
    )''')
    conn.commit()
    conn.close()

def save_book_progress(user_id, title, current_page, total_pages):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''INSERT INTO book_tracking (user_id, title, current_page, total_pages, updated_at)
                 VALUES (?, ?, ?, ?, ?)
                 ON CONFLICT(user_id) DO UPDATE SET
                    title=excluded.title, current_page=excluded.current_page,
                    total_pages=excluded.total_pages, updated_at=excluded.updated_at''',
              (user_id, title, current_page, total_pages, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def get_book_progress(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT title, current_page, total_pages, updated_at FROM book_tracking WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row

def update_book_page(user_id, new_page):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("UPDATE book_tracking SET current_page=?, updated_at=? WHERE user_id=?",
              (new_page, datetime.now().strftime("%Y-%m-%d %H:%M"), user_id))
    conn.commit()
    conn.close()

def index_music_track(artist, title, file_id, message_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    # از تکرار جلوگیری شود (همان message_id دوباره ایندکس نشود)
    c.execute("SELECT id FROM music_index WHERE message_id=?", (message_id,))
    if c.fetchone():
        conn.close()
        return
    c.execute("INSERT INTO music_index (artist, title, file_id, message_id, added_at) VALUES (?, ?, ?, ?, ?)",
              (artist, title, file_id, message_id, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def search_music_index(query_text):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    like = f"%{query_text}%"
    c.execute("SELECT artist, title, file_id FROM music_index WHERE artist LIKE ? OR title LIKE ? ORDER BY id DESC LIMIT 5",
              (like, like))
    rows = c.fetchall()
    conn.close()
    return rows

# ─── پارس کپشن پست کانال برای استخراج خواننده/اسم آهنگ ──────
def parse_music_caption(caption):
    """
    فرمت پست‌های کانال:
    🎤  #نام_خواننده
    🎼    نام آهنگ
    [بقیه متن...]
    """
    if not caption:
        return None, None
    artist, title = None, None
    for line in caption.split("\n"):
        line = line.strip()
        if line.startswith("🎤"):
            artist = line.replace("🎤", "").replace("#", "").strip()
        elif line.startswith("🎼"):
            title = line.replace("🎼", "").strip()
    return artist, title

# ─── جستجو در منابع رایگان و قانونی (Jamendo API) ────────────
# Jamendo: کاتالوگ +۵۰۰,۰۰۰ ترک با مجوز Creative Commons، رایگان
# برای فعال‌سازی کامل: یک client_id رایگان از https://devportal.jamendo.com بگیرید
# و در متغیر محیطی JAMENDO_CLIENT_ID قرار دهید. بدون آن، این بخش نتیجه‌ای برنمی‌گرداند
# و مستقیماً به مرحله «اطلاع به ادمین» می‌رود (که همچنان کار می‌کند).
JAMENDO_CLIENT_ID = os.environ.get("JAMENDO_CLIENT_ID", "")

async def search_free_legal_music(query_text):
    """
    جستجو در Jamendo - فقط موزیک‌هایی با مجوز Creative Commons (کاملاً قانونی)
    خروجی: (title, artist, download_url) یا None
    """
    if not JAMENDO_CLIENT_ID:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.jamendo.com/v3.0/tracks/",
                params={
                    "client_id": JAMENDO_CLIENT_ID,
                    "format": "json",
                    "limit": 1,
                    "namesearch": query_text,
                }
            )
            data = r.json()
            results = data.get("results", [])
            if not results:
                return None
            track = results[0]
            title = track.get("name", "نامشخص")
            artist = track.get("artist_name", "نامشخص")
            download_url = track.get("audiodownload") or track.get("audio")
            if not download_url:
                return None
            return title, artist, download_url
    except Exception as e:
        print(f"خطای جستجوی موزیک رایگان: {e}")
        return None

# ─── منوها ────────────────────────────────────────────────────
def fun_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏋️ سلامت و ورزش", callback_data="menu_health")],
        [InlineKeyboardButton("📖 کتاب‌خوانی", callback_data="menu_book")],
        [InlineKeyboardButton("🎵 موزیک", callback_data="menu_music")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

def health_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚖️ ثبت وزن", callback_data="health_weight")],
        [InlineKeyboardButton("📊 نمودار وزن", callback_data="health_chart")],
        [InlineKeyboardButton("🏃 برنامه ورزشی", callback_data="health_exercise")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_fun")],
    ])

def book_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ ثبت کتاب جدید", callback_data="book_new")],
        [InlineKeyboardButton("📊 پیگیری کتاب فعلی", callback_data="book_progress")],
        [InlineKeyboardButton("💡 معرفی کتاب جدید", callback_data="book_suggest")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_fun")],
    ])

def music_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 جستجوی آهنگ", callback_data="music_search")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_fun")],
    ])

BOOK_SUGGESTIONS = [
    ("ملت عشق", "الیف شافاک", "عاشقانه/فلسفی"),
    ("کیمیاگر", "پائولو کوئیلو", "فلسفی/الهام‌بخش"),
    ("صد سال تنهایی", "گابریل گارسیا مارکز", "رئالیسم جادویی"),
    ("بوف کور", "صادق هدایت", "ادبیات کلاسیک فارسی"),
    ("جنایت و مکافات", "داستایوفسکی", "روان‌شناختی/کلاسیک"),
    ("کافکا در کنار دریا", "هاروکی موراکامی", "رئالیسم جادویی مدرن"),
    ("شازده کوچولو", "آنتوان دو سنت اگزوپری", "فلسفی/کودک و بزرگسال"),
    ("هزار خورشید تابان", "خالد حسینی", "درام اجتماعی"),
]

# ─── هندلر دکمه‌ها ────────────────────────────────────────────
async def handle_fun(query, user_id):
    data = query.data

    if data == "menu_fun":
        await query.edit_message_text("🎮 سرگرمی:", reply_markup=fun_menu())

    elif data == "back_fun":
        clear_state(user_id)
        await query.edit_message_text("🎮 سرگرمی:", reply_markup=fun_menu())

    # ─── سلامت و ورزش (همان منطق قبلی) ───
    elif data == "menu_health":
        await query.edit_message_text("🏋️ سلامت و ورزش:", reply_markup=health_menu())

    elif data.startswith("health_"):
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

    # ─── کتاب‌خوانی ───
    elif data == "menu_book":
        await query.edit_message_text("📖 کتاب‌خوانی:", reply_markup=book_menu())

    elif data == "book_new":
        set_state(user_id, "book_title")
        await query.edit_message_text(
            "📖 نام کتابی که شروع به خواندنش می‌کنید را وارد کنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="menu_book")]])
        )

    elif data == "book_progress":
        progress = get_book_progress(user_id)
        if not progress:
            await query.edit_message_text(
                "📭 هنوز کتابی ثبت نکرده‌اید.",
                reply_markup=book_menu()
            )
            return
        title, current_page, total_pages, updated_at = progress
        percent = (current_page / total_pages * 100) if total_pages else 0
        bar_filled = int(percent // 10)
        bar = "🟩" * bar_filled + "⬜️" * (10 - bar_filled)
        text = (
            f"📖 {title}\n"
            f"{bar} {percent:.0f}%\n\n"
            f"صفحه فعلی: {current_page} از {total_pages}\n"
            f"آخرین به‌روزرسانی: {updated_at}"
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ به‌روزرسانی صفحه", callback_data="book_update_page")],
                [InlineKeyboardButton("🔙 برگشت", callback_data="menu_book")],
            ])
        )

    elif data == "book_update_page":
        progress = get_book_progress(user_id)
        if not progress:
            await query.answer("ابتدا یک کتاب ثبت کنید.", show_alert=True)
            return
        set_state(user_id, "book_new_page")
        await query.edit_message_text("📄 شماره صفحه فعلی را وارد کنید:")

    elif data == "book_suggest":
        import random
        title, author, genre = random.choice(BOOK_SUGGESTIONS)
        await query.edit_message_text(
            f"💡 پیشنهاد کتاب\n\n"
            f"📕 {title}\n"
            f"✍️ نویسنده: {author}\n"
            f"🏷 ژانر: {genre}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 پیشنهاد دیگر", callback_data="book_suggest")],
                [InlineKeyboardButton("🔙 برگشت", callback_data="menu_book")],
            ])
        )

    # ─── موزیک ───
    elif data == "menu_music":
        await query.edit_message_text(
            f"🎵 موزیک\n\n"
            f"نام آهنگ یا خواننده را تایپ کنید تا جستجو کنم.\n\n"
            f"اول در کانال موزیک جستجو می‌کنم، اگر نبود سراغ "
            f"منابع رایگان و قانونی می‌روم.",
            reply_markup=music_menu()
        )

    elif data == "music_search":
        set_state(user_id, "music_query")
        await query.edit_message_text(
            "🔍 نام آهنگ یا خواننده را وارد کنید:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="menu_music")]])
        )

async def handle_fun_message(user_id, text, update):
    state, data = get_state(user_id)
    text = text.strip()

    if state == "book_title":
        set_state(user_id, "book_total_pages", text)
        await update.message.reply_text("📄 تعداد کل صفحات کتاب را وارد کنید:")
        return True

    elif state == "book_total_pages":
        try:
            total_pages = int(text)
            title = data
            save_book_progress(user_id, title, 0, total_pages)
            await update.message.reply_text(
                f"✅ کتاب «{title}» ثبت شد!\nهر وقت پیشرفت داشتید، از منوی کتاب‌خوانی به‌روزرسانی کنید.",
                reply_markup=book_menu()
            )
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    elif state == "book_new_page":
        try:
            new_page = int(text)
            progress = get_book_progress(user_id)
            if progress and new_page > progress[2]:
                await update.message.reply_text(
                    f"❌ شماره صفحه نمی‌تواند بیشتر از تعداد کل صفحات ({progress[2]}) باشد:"
                )
                return True
            update_book_page(user_id, new_page)
            await update.message.reply_text("✅ پیشرفت به‌روزرسانی شد!", reply_markup=book_menu())
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    elif state == "music_query":
        query_text = text
        await update.message.reply_text("⏳ در حال جستجو...")

        # ۱) جستجو در ایندکس کانال
        results = search_music_index(query_text)
        if results:
            artist, title, file_id = results[0]
            await update.message.reply_audio(
                audio=file_id,
                caption=f"🎤 {artist}\n🎼 {title}\n\nاز کانال: @{MUSIC_CHANNEL_USERNAME}"
            )
            clear_state(user_id)
            await update.message.reply_text("نتیجه پیدا شد ✅", reply_markup=music_menu())
            return True

        # ۲) جستجو در منابع رایگان و قانونی
        free_result = await search_free_legal_music(query_text)
        if free_result:
            title, artist, url = free_result
            await update.message.reply_text(
                f"🎵 پیدا شد در منبع رایگان و قانونی:\n\n"
                f"🎼 {title}\n🎤 {artist}\n\n🔗 لینک دانلود:\n{url}",
                reply_markup=music_menu()
            )
            clear_state(user_id)
            return True

        # ۳) پیدا نشد - اطلاع به ادمین
        await update.message.reply_text(
            f"❌ متأسفانه «{query_text}» در کانال یا منابع رایگان پیدا نشد.\n\n"
            f"درخواست شما برای ادمین ارسال شد تا در صورت داشتن مجوز پخش، اضافه شود.",
            reply_markup=music_menu()
        )
        try:
            requester = update.effective_user
            await update.get_bot().send_message(
                ADMIN_ID,
                f"🎵 درخواست آهنگ بدون نتیجه:\n\n"
                f"🔍 جستجو: {query_text}\n"
                f"👤 از طرف: {requester.first_name} (@{requester.username or 'ندارد'})\n\n"
                f"در صورت داشتن مجوز پخش، لطفاً به کانال @{MUSIC_CHANNEL_USERNAME} اضافه کنید."
            )
        except Exception as e:
            print(f"خطا در اطلاع به ادمین: {e}")
        clear_state(user_id)
        return True

    return False

# ─── ایندکس کردن پست‌های جدید کانال موزیک (خودکار) ──────────
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    chat_username = post.chat.username
    if not chat_username or chat_username.lower() != MUSIC_CHANNEL_USERNAME.lower():
        return
    if not post.audio:
        return
    caption = post.caption or ""
    artist, title = parse_music_caption(caption)
    if not artist:
        artist = post.audio.performer or "نامشخص"
    if not title:
        title = post.audio.title or "نامشخص"
    index_music_track(artist, title, post.audio.file_id, post.message_id)
    print(f"🎵 آهنگ جدید ایندکس شد: {artist} - {title}")

# ═══════════════ پایان MODULE: FUN ══════════════════════════════





# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: LEARNING                            ║
# ╚══════════════════════════════════════════════════════════════╝

def learn_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔤 کلمه انگلیسی روزانه", callback_data="learn_word")],
        [InlineKeyboardButton("📘 خلاصه کتاب", callback_data="learn_book")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

async def handle_learning(query, user_id):
    data = query.data
    if data == "menu_learn":
        await query.edit_message_text("📚 یادگیری:", reply_markup=learn_menu())
    else:
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

# ═══════════════ پایان MODULE: LEARNING ════════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: SETTINGS                            ║
# ╚══════════════════════════════════════════════════════════════╝

def settings_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 پروفایل من", callback_data="set_profile")],
        [InlineKeyboardButton("📅 تاریخ انقضا", callback_data="set_expiry")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

async def handle_settings(query, user_id):
    data = query.data
    if data == "menu_settings":
        await query.edit_message_text("⚙️ تنظیمات:", reply_markup=settings_menu())
    elif data == "set_profile":
        db_user = get_user(user_id)
        expires = db_user[5] if db_user else "---"
        await query.edit_message_text(
            f"👤 پروفایل من\n\n🆔 آیدی: {user_id}\n📅 انقضا: {expires}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="menu_settings")]]))
    elif data == "set_expiry":
        db_user = get_user(user_id)
        expires = db_user[5] if db_user else "---"
        await query.answer(f"📅 انقضا: {expires}", show_alert=True)
    else:
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

# ═══════════════ پایان MODULE: SETTINGS ════════════════════════

# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: TRANSLATE                           ║
# ║  برای حذف: این بلوک رو پاک کن                               ║
# ║  + خط translate رو از main_menu پاک کن                      ║
# ║  + خط handle_translate رو از ROUTER پاک کن                  ║
# ║  + خط state translate رو از message_handler پاک کن          ║
# ╚══════════════════════════════════════════════════════════════╝

def translate_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔤 ترجمه متن", callback_data="tr_start")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

async def do_translate(text):
    try:
        async with httpx.AsyncClient(
            timeout=20
        ) as client:
            persian_chars = sum(1 for c in text if '\u0600' <= c <= '\u06FF')
            is_persian = persian_chars > len(text) * 0.3

            if is_persian:
                target_lang = "en"
                direction = "🇮🇷 فارسی → 🇬🇧 English"
            else:
                target_lang = "fa"
                direction = "🇬🇧 English → 🇮🇷 فارسی"

            response = await client.get(
                "https://api.mymemory.translated.net/get",
                params={
                    "q": text,
                    "langpair": f"{'fa' if is_persian else 'en'}|{target_lang}"
                }
            )
            data = response.json()
            translated = data["responseData"]["translatedText"]

            # رفع کاراکترهای HTML مثل &#10; (خط جدید) و &amp; و...
            import html
            translated = html.unescape(translated)

            return direction, translated

    except Exception as e:
        print(f"Translate error: {e}")
        return None, None

async def handle_translate(query, user_id):
    data = query.data

    if data == "menu_translate":
        await query.edit_message_text(
            "🌐 ماژول ترجمه\n\nمتن خود را بنویسید — اگر فارسی باشد به انگلیسی و اگر انگلیسی باشد به فارسی ترجمه می‌شود.",
            reply_markup=translate_menu()
        )

    elif data == "tr_start":
        set_state(user_id, "tr_waiting")
        await query.edit_message_text(
            "✍️ متن مورد نظر را تایپ کنید:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 برگشت", callback_data="menu_translate")]
            ])
        )

async def handle_translate_message(user_id, text, update):
    state, _ = get_state(user_id)

    if state == "tr_waiting":
        await update.message.reply_text("⏳ در حال ترجمه...")
        direction, translated = await do_translate(text)

        if translated:
            await update.message.reply_text(
                f"🌐 {direction}\n\n"
                f"📝 متن اصلی:\n{text}\n\n"
                f"✅ ترجمه:\n{translated}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 ترجمه جدید", callback_data="tr_start")],
                    [InlineKeyboardButton("🔙 برگشت به منو", callback_data="menu_translate")],
                ])
            )
        else:
            await update.message.reply_text(
                "❌ خطا در ترجمه. دوباره امتحان کنید.",
                reply_markup=translate_menu()
            )
        clear_state(user_id)
        return True

    return False

# ═══════════════ پایان MODULE: TRANSLATE ═══════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: POSITION SIZE                       ║
# ║  برای حذف: این بلوک رو پاک کن                               ║
# ║  + خط ps_size رو از financial_menu پاک کن                   ║
# ║  + خط ps_ رو از ROUTER اصلی پاک کن                          ║
# ║  + state های ps_ رو از message_handler پاک کن               ║
# ╚══════════════════════════════════════════════════════════════╝

# ─── مشخصات قراردادی هر بازار برای محاسبه ارزش هر واحد حرکت قیمت ──
# pip_size: کوچک‌ترین واحد حرکت قیمت استاندارد آن نماد
# contract_size: اندازه ۱ لات استاندارد (تعداد واحد ارز پایه/کالا)
FX_CONTRACT_SPECS = {
    "EURUSD": {"pip_size": 0.0001, "contract_size": 100000, "quote_is_usd": True},
    "GBPUSD": {"pip_size": 0.0001, "contract_size": 100000, "quote_is_usd": True},
    "AUDUSD": {"pip_size": 0.0001, "contract_size": 100000, "quote_is_usd": True},
    "NZDUSD": {"pip_size": 0.0001, "contract_size": 100000, "quote_is_usd": True},
    "USDCAD": {"pip_size": 0.0001, "contract_size": 100000, "quote_is_usd": False},
    "USDJPY": {"pip_size": 0.01,   "contract_size": 100000, "quote_is_usd": False},
    "USDCHF": {"pip_size": 0.0001, "contract_size": 100000, "quote_is_usd": False},
    "EURGBP": {"pip_size": 0.0001, "contract_size": 100000, "quote_is_usd": False},
    "EURJPY": {"pip_size": 0.01,   "contract_size": 100000, "quote_is_usd": False},
    "GBPJPY": {"pip_size": 0.01,   "contract_size": 100000, "quote_is_usd": False},
}
METAL_CONTRACT_SPECS = {
    "GOLD": {"contract_size": 100},     # 1 لات طلا = 100 اونس
    "XAUUSD": {"contract_size": 100},
    "SILVER": {"contract_size": 5000},  # 1 لات نقره = 5000 اونس
    "XAGUSD": {"contract_size": 5000},
}

def ps_classify_symbol(symbol):
    """تشخیص نوع نماد برای انتخاب فرمول محاسبه: forex_pair / metal / crypto / iran / forex_other"""
    s = symbol.upper()
    if s in FX_CONTRACT_SPECS:
        return "forex_pair"
    if s in METAL_CONTRACT_SPECS:
        return "metal"
    if s in CRYPTO_SYMBOLS:
        return "crypto"
    if s in IRAN_SYMBOLS:
        return "iran"
    return "forex_other"

async def ps_calculate_position(symbol, direction, entry_price, sl_price, risk_usd):
    """
    محاسبه حجم پیشنهادی بر اساس مقدار ریسک دلاری.
    خروجی: dict شامل risk_distance, unit_value, lots/qty, explanation
    """
    symbol = symbol.upper()
    category = ps_classify_symbol(symbol)
    risk_distance = abs(entry_price - sl_price)

    if risk_distance == 0:
        return None

    if category == "forex_pair":
        spec = FX_CONTRACT_SPECS[symbol]
        contract_size = spec["contract_size"]
        if spec["quote_is_usd"]:
            # مثل EURUSD: ارزش حرکت قیمت برای 1 لات کامل = فاصله قیمت × اندازه قرارداد
            value_per_lot = risk_distance * contract_size
        else:
            # مثل USDJPY/USDCAD: ارز Quote دلار نیست، باید تقسیم بر نرخ لحظه‌ای جفت‌ارز شود
            # تا حرکت قیمت (که به واحد Quote است) به دلار تبدیل شود
            live_price, _ = await get_price_by_symbol(symbol, "forex")
            reference_price = live_price if live_price else entry_price
            value_per_lot = (risk_distance * contract_size) / reference_price
        lots = risk_usd / value_per_lot
        return {
            "category": "forex_pair", "risk_distance": risk_distance,
            "lots": lots, "value_per_lot": value_per_lot, "unit": "لات استاندارد"
        }

    elif category == "metal":
        spec = METAL_CONTRACT_SPECS[symbol]
        contract_size = spec["contract_size"]
        value_per_lot = risk_distance * contract_size
        lots = risk_usd / value_per_lot
        return {
            "category": "metal", "risk_distance": risk_distance,
            "lots": lots, "value_per_lot": value_per_lot, "unit": "لات استاندارد"
        }

    elif category == "crypto":
        qty = risk_usd / risk_distance
        return {
            "category": "crypto", "risk_distance": risk_distance,
            "lots": qty, "value_per_lot": risk_distance, "unit": f"واحد {symbol}"
        }

    elif category == "iran":
        qty = risk_usd / risk_distance
        return {
            "category": "iran", "risk_distance": risk_distance,
            "lots": qty, "value_per_lot": risk_distance, "unit": "واحد (ریال‌محور)"
        }

    else:  # forex_other - جفت ارز ناشناخته با pip استاندارد فرض می‌شود
        contract_size = 100000
        live_price, _ = await get_price_by_symbol(symbol, "forex")
        reference_price = live_price if live_price else entry_price
        value_per_lot = (risk_distance * contract_size) / reference_price if reference_price else 0
        lots = risk_usd / value_per_lot if value_per_lot else 0
        return {
            "category": "forex_other", "risk_distance": risk_distance,
            "lots": lots, "value_per_lot": value_per_lot, "unit": "لات استاندارد (تقریبی)"
        }

# ─── منوها ────────────────────────────────────────────────────
def position_size_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📐 محاسبه جدید", callback_data="ps_new")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_financial")],
    ])

def ps_direction_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Long (خرید)", callback_data="ps_dir_long")],
        [InlineKeyboardButton("📉 Short (فروش)", callback_data="ps_dir_short")],
    ])

def ps_risk_type_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 مقدار دلاری ثابت", callback_data="ps_risk_fixed")],
        [InlineKeyboardButton("📊 درصدی از بالانس", callback_data="ps_risk_percent")],
    ])

# ─── هندلر دکمه‌ها ────────────────────────────────────────────
async def handle_position_size(query, user_id):
    data = query.data

    if data == "ps_size":
        await query.edit_message_text(
            "📐 محاسبه میزان خرید (Position Sizing)\n\n"
            "با گرفتن نقطه ورود، حد ضرر و میزان ریسکی که می‌خواهید "
            "بپذیرید (دلاری یا درصدی از بالانس)، حجم مناسب معامله "
            "(لات استاندارد یا مقدار واحد) را محاسبه می‌کند.",
            reply_markup=position_size_menu()
        )

    elif data == "ps_new":
        set_state(user_id, "ps_symbol")
        await query.edit_message_text(
            "📐 محاسبه جدید\n\nنماد ارز را وارد کنید:\nمثال: `EURUSD` `GOLD` `BTC` `USD`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="ps_size")]])
        )

    elif data in ["ps_dir_long", "ps_dir_short"]:
        direction = "long" if data == "ps_dir_long" else "short"
        state, sdata = get_state(user_id)
        if state == "ps_direction":
            set_state(user_id, "ps_entry_price", f"{sdata}|{direction}")
            await query.edit_message_text(
                f"✅ جهت: {'Long 📈' if direction=='long' else 'Short 📉'}\n\nقیمت ورود را وارد کنید:"
            )

    elif data in ["ps_risk_fixed", "ps_risk_percent"]:
        state, sdata = get_state(user_id)
        if state == "ps_risk_type":
            if data == "ps_risk_fixed":
                set_state(user_id, "ps_risk_amount", f"{sdata}|fixed")
                await query.edit_message_text("💵 مقدار ریسک به دلار را وارد کنید:\nمثال: `10`", parse_mode="Markdown")
            else:
                set_state(user_id, "ps_balance", f"{sdata}|percent")
                await query.edit_message_text("💰 بالانس حساب را به دلار وارد کنید:\nمثال: `1000`", parse_mode="Markdown")

async def handle_position_size_message(user_id, text, update):
    state, data = get_state(user_id)
    text = text.strip()

    if state == "ps_symbol":
        symbol = text.upper()
        set_state(user_id, "ps_direction", symbol)
        await update.message.reply_text("جهت معامله را انتخاب کنید:", reply_markup=ps_direction_menu())
        return True

    elif state == "ps_entry_price":
        try:
            entry_price = float(text.replace(",", ""))
            set_state(user_id, "ps_sl_price", f"{data}|{entry_price}")
            await update.message.reply_text("قیمت حد ضرر را وارد کنید:")
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    elif state == "ps_sl_price":
        try:
            sl_price = float(text.replace(",", ""))
            parts = data.split("|")
            symbol, direction, entry_price = parts[0], parts[1], float(parts[2])

            if (direction == "long" and sl_price >= entry_price) or (direction == "short" and sl_price <= entry_price):
                await update.message.reply_text("❌ حد ضرر با جهت معامله همخوانی ندارد.\nدوباره وارد کنید:")
                return True

            set_state(user_id, "ps_risk_type", f"{symbol}|{direction}|{entry_price}|{sl_price}")
            await update.message.reply_text("روش تعیین ریسک را انتخاب کنید:", reply_markup=ps_risk_type_menu())
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    elif state == "ps_risk_amount":
        try:
            risk_usd = float(text.replace(",", ""))
            if risk_usd <= 0:
                await update.message.reply_text("❌ مقدار ریسک باید بزرگ‌تر از صفر باشد:")
                return True
            parts = data.split("|")
            symbol, direction, entry_price, sl_price, risk_mode = parts
            await run_position_size_final(user_id, update, symbol, direction,
                                           float(entry_price), float(sl_price), risk_usd)
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    elif state == "ps_balance":
        try:
            balance = float(text.replace(",", ""))
            if balance <= 0:
                await update.message.reply_text("❌ بالانس باید بزرگ‌تر از صفر باشد:")
                return True
            set_state(user_id, "ps_percent", f"{data}|{balance}")
            await update.message.reply_text("درصد ریسک مجاز را وارد کنید:\nمثال: `2`")
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    elif state == "ps_percent":
        try:
            percent = float(text.replace(",", ""))
            if percent <= 0 or percent > 100:
                await update.message.reply_text("❌ درصد باید بین 0 و 100 باشد:")
                return True
            parts = data.split("|")
            symbol, direction, entry_price, sl_price, risk_mode, balance = parts
            risk_usd = float(balance) * (percent / 100)
            await run_position_size_final(user_id, update, symbol, direction,
                                           float(entry_price), float(sl_price), risk_usd,
                                           balance=float(balance), percent=percent)
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    return False

async def run_position_size_final(user_id, update, symbol, direction, entry_price, sl_price,
                                    risk_usd, balance=None, percent=None):
    await update.message.reply_text("⏳ در حال محاسبه...")

    result = await ps_calculate_position(symbol, direction, entry_price, sl_price, risk_usd)
    if not result:
        await update.message.reply_text("❌ خطا در محاسبه (فاصله ریسک صفر است).", reply_markup=position_size_menu())
        clear_state(user_id)
        return

    risk_line = f"💵 مقدار ریسک: ${risk_usd:,.2f}"
    if balance and percent:
        risk_line += f"\n💰 بالانس: ${balance:,.2f} | درصد ریسک: {percent}%"

    text = (
        f"📐 محاسبه میزان خرید — {symbol} ({'Long' if direction=='long' else 'Short'})\n"
        f"`━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
        f"ورود: {entry_price}\n"
        f"حد ضرر: {sl_price}\n"
        f"فاصله ریسک: {result['risk_distance']:.5f}\n"
        f"`━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
        f"{risk_line}\n"
        f"`━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
        f"📦 حجم پیشنهادی: {result['lots']:.4f} {result['unit']}\n"
        f"`━━━━━━━━━━━━━━━━━━━━━━━━━━`"
    )

    if result["category"] == "forex_other":
        text += "\n\n⚠️ این نماد در لیست دقیق ربات نبود؛ محاسبه با فرض pip استاندارد فارکس انجام شده و ممکن است تقریبی باشد."

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=position_size_menu())
    clear_state(user_id)

# ═══════════════ پایان MODULE: POSITION SIZE ════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: BACKTEST                            ║
# ║  برای حذف: این بلوک رو پاک کن                               ║
# ║  + خط backtest رو از financial_menu پاک کن                  ║
# ║  + خط fin_backtest/bt_ رو از ROUTER اصلی پاک کن             ║
# ║  + state های bt_ رو از message_handler پاک کن               ║
# ╚══════════════════════════════════════════════════════════════╝

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill

IRAN_TZ_OFFSET = timedelta(hours=3, minutes=30)
R_LEVELS = [0.5, 1, 1.5, 2, 3, 4, 8, 10]

def to_iran_time(dt_naive_utc_like):
    """تبدیل datetime (که از Yahoo می‌آید، معمولاً UTC) به ساعت ایران برای نمایش"""
    return dt_naive_utc_like + IRAN_TZ_OFFSET

# ─── دیتابیس استراتژی ───────────────────────────────────────
def init_backtest_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS bt_strategies (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER,
        name       TEXT,
        rules_json TEXT,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS bt_results (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        batch_id    TEXT,
        symbol      TEXT,
        direction   TEXT,
        entry_time  TEXT,
        entry_price REAL,
        sl_price    REAL,
        strategy    TEXT,
        max_r       REAL,
        result_r    REAL,
        exit_time   TEXT,
        exit_price  REAL,
        status      TEXT,
        created_at  TEXT
    )''')
    conn.commit()
    conn.close()

def save_strategy(user_id, name, rules):
    import json
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO bt_strategies (user_id, name, rules_json, created_at) VALUES (?, ?, ?, ?)",
              (user_id, name, json.dumps(rules), datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def get_strategies(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT id, name, rules_json FROM bt_strategies WHERE user_id=? ORDER BY id DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_strategy_by_id(sid):
    import json
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT name, rules_json FROM bt_strategies WHERE id=?", (sid,))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0], json.loads(row[1])
    return None, None

def save_bt_result(user_id, batch_id, symbol, direction, entry_time, entry_price,
                    sl_price, strategy_name, max_r, result_r, exit_time, exit_price, status):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''INSERT INTO bt_results
        (user_id, batch_id, symbol, direction, entry_time, entry_price, sl_price,
         strategy, max_r, result_r, exit_time, exit_price, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (user_id, batch_id, symbol, direction, entry_time, entry_price, sl_price,
         strategy_name, max_r, result_r, exit_time, exit_price, status,
         datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

def get_batch_results(user_id, batch_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT * FROM bt_results WHERE user_id=? AND batch_id=? ORDER BY id", (user_id, batch_id))
    rows = c.fetchall()
    conn.close()
    return rows

# ─── دریافت کندل‌های تاریخی (بدون تایم‌فریم روزانه) ─────────
async def get_historical_candles(symbol, entry_date, entry_hm, interval):
    """entry_date: YYYY-MM-DD | entry_hm: HH:MM (به وقت ایران) | interval: 1m/15m/1h"""
    try:
        entry_dt_iran = datetime.strptime(f"{entry_date} {entry_hm}", "%Y-%m-%d %H:%M")
        entry_dt_utc = entry_dt_iran - IRAN_TZ_OFFSET
        days_ago = (datetime.now() - entry_dt_utc).days

        limits = {"1m": 7, "15m": 60, "1h": 730}
        max_days = limits.get(interval)
        if max_days is None or days_ago > max_days:
            return None, None, f"⚠️ تایم‌فریم انتخابی فقط تا {max_days} روز گذشته را پشتیبانی می‌کند.\nتاریخ شما {days_ago} روز پیش است."

        range_map = {
            "1m": f"{min(days_ago + 1, 7)}d",
            "15m": f"{min(days_ago + 1, 60)}d",
            "1h": f"{min(days_ago + 5, 730)}d",
        }
        range_param = range_map[interval]

        yahoo_sym = FOREX_YAHOO.get(symbol.upper())
        if not yahoo_sym and symbol.upper() in GOLDAPI_SYMBOLS:
            yahoo_sym = "GC%3DF" if symbol.upper() in ["GOLD", "XAUUSD"] else "SI%3DF"
        if not yahoo_sym:
            yahoo_sym = symbol.upper() + "%3DX"

        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}",
                params={"interval": interval, "range": range_param}
            )
            data = r.json()["chart"]["result"][0]
            timestamps = data["timestamp"]
            quotes = data["indicators"]["quote"][0]

            candles = []
            entry_ts = entry_dt_utc.timestamp()
            for i, ts in enumerate(timestamps):
                if ts >= entry_ts:
                    high = quotes["high"][i]
                    low = quotes["low"][i]
                    close = quotes["close"][i]
                    if high is not None and low is not None:
                        candles.append({
                            "time_utc": datetime.fromtimestamp(ts),
                            "high": high, "low": low, "close": close
                        })
            if not candles:
                return None, None, "❌ داده‌ای برای این بازه زمانی یافت نشد."
            return candles, entry_dt_utc, None
    except Exception as e:
        print(f"Historical data error: {e}")
        return None, None, "❌ خطا در دریافت داده‌های تاریخی."

# ─── موتور شبیه‌سازی با قوانین سفارشی کاربر ─────────────────
def run_custom_simulation(candles, entry_price, sl_price, direction, strategy_rules):
    """
    strategy_rules: dict مثل {"0.5": None, "1": {"type": "r_trail", "value": 0.2}, ...}
    مقدار None یعنی SL تغییر نمی‌کند در آن سطح
    مقدار {"type": "r_trail", "value": X} یعنی SL روی سطح RX قرار می‌گیرد
        (مثلاً value=0 یعنی نقطه ورود، value=0.2 یعنی R0.2 از نقطه ورود)
    در R10 همیشه خروج کامل
    """
    R = abs(entry_price - sl_price)
    is_long = direction == "long"

    def level(r_mult):
        return entry_price + (r_mult * R) if is_long else entry_price - (r_mult * R)

    current_sl = sl_price
    max_r_reached = 0
    triggered_levels = set()

    for candle in candles:
        high, low = candle["high"], candle["low"]

        hit_sl = (low <= current_sl) if is_long else (high >= current_sl)
        if hit_sl:
            exit_r = (current_sl - entry_price) / R if is_long else (entry_price - current_sl) / R
            return {
                "exit_price": current_sl, "exit_time_utc": candle["time_utc"],
                "result_r": round(exit_r, 2), "max_r": max_r_reached, "status": "closed"
            }

        reached = high if is_long else low
        for r_mult in R_LEVELS:
            if r_mult in triggered_levels:
                continue
            target_level = level(r_mult)
            condition = (reached >= target_level) if is_long else (reached <= target_level)
            if condition:
                triggered_levels.add(r_mult)
                max_r_reached = r_mult

                if r_mult == 10:
                    return {
                        "exit_price": level(10), "exit_time_utc": candle["time_utc"],
                        "result_r": 10.0, "max_r": 10.0, "status": "closed"
                    }

                rule = strategy_rules.get(str(r_mult))
                if isinstance(rule, dict) and rule.get("type") == "r_trail":
                    current_sl = level(rule["value"])
                # rule None -> بدون تغییر

    last_close = candles[-1]["close"] if candles else entry_price
    open_r = (last_close - entry_price) / R if is_long else (entry_price - last_close) / R
    return {
        "exit_price": None, "exit_time_utc": None,
        "result_r": round(open_r, 2), "max_r": max_r_reached, "status": "open"
    }

# ─── ساخت فایل اکسل ──────────────────────────────────────────
def build_excel_report(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Backtest Results"

    headers = ["نماد", "جهت", "تاریخ ورود (ایران)", "قیمت ورود", "SL اولیه",
               "استراتژی", "بالاترین R", "نتیجه (R)", "تاریخ خروج (ایران)",
               "قیمت خروج", "وضعیت"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
        cell.alignment = Alignment(horizontal="center")

    for row in rows:
        (_, _, _, symbol, direction, entry_time, entry_price, sl_price,
         strategy, max_r, result_r, exit_time, exit_price, status, _) = row
        ws.append([
            symbol, "Long" if direction == "long" else "Short",
            entry_time, entry_price, sl_price, strategy, max_r, result_r,
            exit_time or "---", exit_price or "---",
            "بسته شده" if status == "closed" else "باز"
        ])

    for col in ws.columns:
        max_len = max(len(str(c.value)) for c in col if c.value is not None)
        ws.column_dimensions[col[0].column_letter].width = max_len + 4

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

# ─── منوها ────────────────────────────────────────────────────
def backtest_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ بک‌تست جدید", callback_data="bt_new")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_financial")],
    ])

def strategy_choice_menu(user_id):
    strategies = get_strategies(user_id)
    keyboard = []
    for sid, name, _ in strategies[:10]:
        keyboard.append([InlineKeyboardButton(f"📋 {name}", callback_data=f"bt_strat_use_{sid}")])
    keyboard.append([InlineKeyboardButton("🆕 ساخت استراتژی جدید", callback_data="bt_strat_create")])
    keyboard.append([InlineKeyboardButton("🔙 برگشت", callback_data="fin_backtest")])
    return InlineKeyboardMarkup(keyboard)

def r_level_question_menu(r_mult):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله، جابجا کن", callback_data=f"bt_r_yes_{r_mult}")],
        [InlineKeyboardButton("❌ نه، رد شو", callback_data=f"bt_r_no_{r_mult}")],
    ])

def yes_no_menu(yes_cb, no_cb):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله", callback_data=yes_cb)],
        [InlineKeyboardButton("❌ خیر", callback_data=no_cb)],
    ])

def direction_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Long (خرید)", callback_data="bt_dir_long")],
        [InlineKeyboardButton("📉 Short (فروش)", callback_data="bt_dir_short")],
    ])

def timeframe_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1 دقیقه (تا 7 روز پیش)", callback_data="bt_tf_1m")],
        [InlineKeyboardButton("15 دقیقه (تا 60 روز پیش)", callback_data="bt_tf_15m")],
        [InlineKeyboardButton("1 ساعت (تا 2 سال پیش)", callback_data="bt_tf_1h")],
    ])

# ─── هندلر دکمه‌ها ────────────────────────────────────────────
async def handle_backtest(query, user_id):
    data = query.data

    if data == "fin_backtest":
        await query.edit_message_text(
            "📊 بک‌تست مدیریت پوزیشن\n\n"
            "این ابزار با گرفتن نقطه ورود، حد ضرر و استراتژی شخصی شما، "
            "مسیر واقعی قیمت را شبیه‌سازی می‌کند.\n\n"
            "سطوح ریوارد بررسی‌شده:\n"
            "R0.5, R1, R1.5, R2, R3, R4, R8, R10\n"
            "(در R10 همیشه خروج کامل)",
            reply_markup=backtest_menu()
        )

    elif data == "bt_new":
        set_state(user_id, "bt_mode_choice")
        await query.edit_message_text(
            "📊 بک‌تست جدید\n\nنوع بک‌تست را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔹 تکی (یک ارز)", callback_data="bt_mode_single")],
                [InlineKeyboardButton("🔸 گروهی (چند ارز)", callback_data="bt_mode_group")],
            ])
        )

    elif data in ["bt_mode_single", "bt_mode_group"]:
        mode = "single" if data == "bt_mode_single" else "group"
        set_state(user_id, "bt_strategy_choice", mode)
        strategies = get_strategies(user_id)
        if strategies:
            await query.edit_message_text(
                "📋 می‌خواهید از استراتژی قبلی استفاده کنید یا جدید بسازید؟",
                reply_markup=strategy_choice_menu(user_id)
            )
        else:
            set_state(user_id, "bt_strat_r_0.5", f"{mode}|")
            await query.edit_message_text(
                "🆕 ساخت استراتژی جدید\n\nدر سطح R0.5، حد ضرر را جابجا کنم؟",
                reply_markup=r_level_question_menu("0.5")
            )

    elif data == "bt_strat_create":
        state, sdata = get_state(user_id)
        mode = sdata.split("|")[0] if sdata else "single"
        set_state(user_id, "bt_strat_r_0.5", f"{mode}|")
        await query.edit_message_text(
            "🆕 ساخت استراتژی جدید\n\nدر سطح R0.5، حد ضرر را جابجا کنم؟",
            reply_markup=r_level_question_menu("0.5")
        )

    elif data.startswith("bt_strat_use_"):
        sid = int(data.replace("bt_strat_use_", ""))
        name, rules = get_strategy_by_id(sid)
        state, sdata = get_state(user_id)
        mode = sdata.split("|")[0] if sdata else "single"
        if name:
            import json
            set_state(user_id, "bt_symbol", f"{mode}|{name}|{json.dumps(rules)}")
            await query.edit_message_text(
                f"✅ استراتژی «{name}» انتخاب شد.\n\n"
                f"نماد ارز را وارد کنید:\nمثال: `EURUSD` `GOLD` `USDCAD`",
                parse_mode="Markdown"
            )

    elif data.startswith("bt_r_yes_") or data.startswith("bt_r_no_"):
        is_yes = data.startswith("bt_r_yes_")
        r_mult = data.replace("bt_r_yes_", "").replace("bt_r_no_", "")
        state, sdata = get_state(user_id)

        if is_yes:
            set_state(user_id, f"bt_strat_slval_{r_mult}", sdata)
            await query.edit_message_text(
                f"🎯 در سطح R{r_mult}، حد ضرر را روی چه ریواردی بگذارم؟\n\n"
                f"یک عدد بین `0` و `{r_mult}` وارد کنید.\n"
                f"مثال: اگر بنویسید `0.2` یعنی حد ضرر روی R0.2 قرار می‌گیرد.\n\n"
                f"برای ریسک‌فری دقیق (نقطه ورود) بنویسید `0`",
                parse_mode="Markdown"
            )
        else:
            mode, rules_str = (sdata.split("|", 1) + [""])[:2]
            import json
            rules = json.loads(rules_str) if rules_str else {}
            rules[r_mult] = None
            next_idx = R_LEVELS.index(float(r_mult)) + 1
            await proceed_to_next_r_level(query, user_id, mode, rules, next_idx)

# عبور به سطح R بعدی یا پایان ساخت استراتژی
async def proceed_to_next_r_level(query_or_update, user_id, mode, rules, next_idx, is_message=False):
    import json
    if next_idx < len(R_LEVELS) - 1:  # R10 سوال ندارد
        next_r = R_LEVELS[next_idx]
        set_state(user_id, f"bt_strat_r_{next_r}", f"{mode}|{json.dumps(rules)}")
        text = f"در سطح R{next_r}، حد ضرر را جابجا کنم؟"
        markup = r_level_question_menu(str(next_r))
        if is_message:
            await query_or_update.message.reply_text(text, reply_markup=markup)
        else:
            await query_or_update.edit_message_text(text, reply_markup=markup)
    else:
        set_state(user_id, "bt_strat_name", f"{mode}|{json.dumps(rules)}")
        text = "✅ تمام سطوح تنظیم شد!\n\nاسمی برای این استراتژی وارد کنید (مثال: استراتژی اصلی):"
        if is_message:
            await query_or_update.message.reply_text(text)
        else:
            await query_or_update.edit_message_text(text)

async def handle_backtest_message(user_id, text, update):
    state, data = get_state(user_id)
    text = text.strip()

    # ─── دریافت مقدار SL برای یک سطح R خاص (به‌صورت عدد ریوارد، نه قیمت) ───
    if state and state.startswith("bt_strat_slval_"):
        r_mult = state.replace("bt_strat_slval_", "")
        import json
        mode, rules_str = (data.split("|", 1) + [""])[:2]
        rules = json.loads(rules_str) if rules_str else {}

        try:
            r_value = float(text.replace(",", ""))
        except:
            await update.message.reply_text(
                f"❌ لطفاً یک عدد معتبر بین `0` و `{r_mult}` وارد کنید:",
                parse_mode="Markdown"
            )
            return True

        current_r = float(r_mult)
        if r_value < 0 or r_value >= current_r:
            await update.message.reply_text(
                f"❌ عدد باید بین `0` و `{r_mult}` باشد (کوچک‌تر از سطح فعلی).\n"
                f"عدد وارد‌شده ({r_value}) خارج از این بازه است.\nدوباره وارد کنید:",
                parse_mode="Markdown"
            )
            return True

        # ذخیره به‌صورت ضریب ریوارد نسبی (نه قیمت مطلق)
        # 0 یعنی دقیقاً نقطه ورود (ریسک‌فری کامل)
        rules[r_mult] = {"type": "r_trail", "value": r_value}

        next_idx = R_LEVELS.index(float(r_mult)) + 1
        await proceed_to_next_r_level(update, user_id, mode, rules, next_idx, is_message=True)
        return True

    # ─── ثبت نام استراتژی ───
    elif state == "bt_strat_name":
        import json
        mode, rules_str = (data.split("|", 1) + [""])[:2]
        rules = json.loads(rules_str) if rules_str else {}
        strategy_name = text
        save_strategy(user_id, strategy_name, rules)
        set_state(user_id, "bt_symbol", f"{mode}|{strategy_name}|{json.dumps(rules)}")
        await update.message.reply_text(
            f"✅ استراتژی «{strategy_name}» ذخیره شد.\n\n"
            f"نماد ارز را وارد کنید:\nمثال: `EURUSD` `GOLD` `USDCAD`",
            parse_mode="Markdown"
        )
        return True

    # ─── نماد ───
    elif state == "bt_symbol":
        symbol = text.upper()
        parts = data.split("|")
        mode, strategy_name, rules_str = parts[0], parts[1], parts[2]
        # اگر این ادامه‌ی یک batch گروهی است، batch_id قبلی را حفظ کن
        if len(parts) > 3 and parts[3].startswith("__BATCH__"):
            batch_id = parts[3].replace("__BATCH__", "")
            set_state(user_id, "bt_direction", f"{mode}|{strategy_name}|{rules_str}|{symbol}|__BATCHID__{batch_id}")
        else:
            set_state(user_id, "bt_direction", f"{mode}|{strategy_name}|{rules_str}|{symbol}")
        await update.message.reply_text("جهت معامله را انتخاب کنید:", reply_markup=direction_menu())
        return True

    # ─── تاریخ ورود ───
    elif state == "bt_entry_date":
        try:
            datetime.strptime(text, "%Y-%m-%d")
            set_state(user_id, "bt_entry_time", f"{data}|{text}")
            await update.message.reply_text(
                "⏰ ساعت ورود را به وقت ایران وارد کنید (فرمت 24 ساعته HH:MM)\nمثال: `14:35`",
                parse_mode="Markdown"
            )
        except:
            await update.message.reply_text("❌ فرمت تاریخ اشتباه است.\nمثال: `2026-03-10`", parse_mode="Markdown")
        return True

    # ─── ساعت ورود ───
    elif state == "bt_entry_time":
        try:
            datetime.strptime(text, "%H:%M")
            set_state(user_id, "bt_entry_price", f"{data}|{text}")
            await update.message.reply_text("قیمت ورود را وارد کنید:")
        except:
            await update.message.reply_text("❌ فرمت ساعت اشتباه است.\nمثال: `14:35` (24 ساعته)", parse_mode="Markdown")
        return True

    # ─── قیمت ورود ───
    elif state == "bt_entry_price":
        try:
            entry_price = float(text.replace(",", ""))
            set_state(user_id, "bt_sl_price", f"{data}|{entry_price}")
            await update.message.reply_text("قیمت حد ضرر اولیه را وارد کنید:")
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    # ─── حد ضرر اولیه ───
    elif state == "bt_sl_price":
        try:
            sl_price = float(text.replace(",", ""))
            parts = data.split("|")
            # اگر مسیر گروهی است، یک قطعه اضافه (__BATCHID__xxx) در میانه وجود دارد
            if len(parts) == 9 and parts[4].startswith("__BATCHID__"):
                mode, strategy_name, rules_str, symbol, batch_tag, direction, entry_date, entry_time, entry_price = parts
            else:
                mode, strategy_name, rules_str, symbol, direction, entry_date, entry_time, entry_price = parts
                batch_tag = None
            entry_price = float(entry_price)

            if (direction == "long" and sl_price >= entry_price) or (direction == "short" and sl_price <= entry_price):
                await update.message.reply_text("❌ حد ضرر با جهت معامله همخوانی ندارد.\nدوباره وارد کنید:")
                return True

            symbol_field = f"{symbol}{batch_tag}" if batch_tag else symbol
            set_state(user_id, "bt_timeframe",
                      f"{mode}|{strategy_name}|{rules_str}|{symbol_field}|{direction}|{entry_date}|{entry_time}|{entry_price}|{sl_price}")
            await update.message.reply_text("⏱ تایم‌فریم را انتخاب کنید:", reply_markup=timeframe_menu())
        except:
            await update.message.reply_text("❌ لطفاً یک عدد وارد کنید:")
        return True

    return False

# ─── دکمه‌های جهت و تایم‌فریم و ادامه/پایان گروهی ────────────
async def handle_backtest_buttons_extra(query, user_id, data):
    if data in ["bt_dir_long", "bt_dir_short"]:
        direction = "long" if data == "bt_dir_long" else "short"
        state, sdata = get_state(user_id)
        if state == "bt_direction":
            set_state(user_id, "bt_entry_date", f"{sdata}|{direction}")
            await query.edit_message_text(
                f"✅ جهت: {'Long 📈' if direction=='long' else 'Short 📉'}\n\n"
                f"تاریخ ورود را وارد کنید (فرمت: YYYY-MM-DD)\nمثال: `2026-03-10`",
                parse_mode="Markdown"
            )
        return True

    if data.startswith("bt_tf_"):
        timeframe = data.replace("bt_tf_", "")
        state, sdata = get_state(user_id)
        if state == "bt_timeframe":
            set_state(user_id, "bt_processing", f"{sdata}|{timeframe}")
            await query.edit_message_text("⏳ در حال دریافت داده‌های تاریخی و شبیه‌سازی...")
            await run_backtest_final(user_id, query)
        return True

    if data == "bt_add_another":
        state, sdata = get_state(user_id)
        parts = sdata.split("|")
        mode, strategy_name, rules_str, batch_id = parts[0], parts[1], parts[2], parts[3]
        set_state(user_id, "bt_symbol", f"{mode}|{strategy_name}|{rules_str}|__BATCH__{batch_id}")
        await query.edit_message_text(
            "➕ نماد ارز بعدی را وارد کنید:\nمثال: `EURUSD` `GOLD` `USDCAD`",
            parse_mode="Markdown"
        )
        return True

    if data == "bt_finish_group":
        state, sdata = get_state(user_id)
        parts = sdata.split("|")
        batch_id = parts[3]
        await send_batch_excel(query, user_id, batch_id)
        clear_state(user_id)
        return True

    return False

async def run_backtest_final(user_id, query):
    state, sdata = get_state(user_id)
    if state != "bt_processing" or not sdata:
        return

    parts = sdata.split("|")
    mode, strategy_name, rules_str, symbol, direction, entry_date, entry_time, entry_price, sl_price, timeframe = parts
    entry_price, sl_price = float(entry_price), float(sl_price)

    import json
    rules = json.loads(rules_str)

    existing_batch_id = None
    if "__BATCHID__" in symbol:
        symbol, batch_tag = symbol.split("__BATCHID__")
        existing_batch_id = batch_tag

    batch_id = existing_batch_id if existing_batch_id else f"{user_id}_{int(datetime.now().timestamp())}"

    candles, entry_dt_utc, error = await get_historical_candles(symbol, entry_date, entry_time, timeframe)
    if error:
        await query.message.reply_text(error, reply_markup=backtest_menu())
        clear_state(user_id)
        return

    result = run_custom_simulation(candles, entry_price, sl_price, direction, rules)
    R = abs(entry_price - sl_price)

    entry_dt_iran = datetime.strptime(f"{entry_date} {entry_time}", "%Y-%m-%d %H:%M")
    exit_dt_iran = to_iran_time(result["exit_time_utc"]) if result["exit_time_utc"] else None

    save_bt_result(
        user_id, batch_id, symbol, direction,
        entry_dt_iran.strftime("%Y-%m-%d %H:%M"), entry_price, sl_price, strategy_name,
        result["max_r"], result["result_r"],
        exit_dt_iran.strftime("%Y-%m-%d %H:%M") if exit_dt_iran else None,
        result["exit_price"], result["status"]
    )

    status_text = "🟢 بسته شده" if result["status"] == "closed" else "🟡 هنوز باز (تا آخرین داده)"
    exit_line = (f"خروج: {result['exit_price']:.5f}\nتاریخ خروج (ایران): {exit_dt_iran.strftime('%Y-%m-%d %H:%M')}"
                 if result["exit_price"] else "هنوز خارج نشده")

    text = (
        f"📊 نتیجه بک‌تست — {symbol} ({'Long' if direction=='long' else 'Short'})\n"
        f"`━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
        f"استراتژی: {strategy_name}\n"
        f"ورود: {entry_price:.5f}\n"
        f"SL اولیه: {sl_price:.5f}  (R = {R:.5f})\n"
        f"تاریخ ورود (ایران): {entry_dt_iran.strftime('%Y-%m-%d %H:%M')}\n"
        f"تایم‌فریم: {timeframe}\n"
        f"`━━━━━━━━━━━━━━━━━━━━━━━━━━`\n"
        f"وضعیت: {status_text}\n"
        f"بالاترین R رسیده: R{result['max_r']}\n"
        f"{exit_line}\n"
        f"نتیجه: {'+' if result['result_r'] >= 0 else ''}{result['result_r']}R\n"
        f"`━━━━━━━━━━━━━━━━━━━━━━━━━━`"
    )

    if mode == "group":
        set_state(user_id, "bt_group_next", f"{mode}|{strategy_name}|{rules_str}|{batch_id}")
        await query.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ ارز بعدی", callback_data="bt_add_another")],
                [InlineKeyboardButton("✅ پایان و دریافت اکسل", callback_data="bt_finish_group")],
            ])
        )
    else:
        await send_single_excel(query, user_id, batch_id, text)
        clear_state(user_id)

async def send_single_excel(query, user_id, batch_id, summary_text):
    rows = get_batch_results(user_id, batch_id)
    excel_buffer = build_excel_report(rows)
    await query.message.reply_text(summary_text, parse_mode="Markdown", reply_markup=backtest_menu())
    await query.message.reply_document(
        document=excel_buffer, filename=f"backtest_{batch_id}.xlsx",
        caption="📎 فایل اکسل نتیجه بک‌تست"
    )

async def send_batch_excel(query, user_id, batch_id):
    rows = get_batch_results(user_id, batch_id)
    excel_buffer = build_excel_report(rows)

    lines = ["📊 خلاصه نتایج بک‌تست گروهی:\n"]
    for row in rows:
        symbol, direction, max_r, result_r, status = row[3], row[4], row[9], row[10], row[13]
        emoji = "🟢" if status == "closed" else "🟡"
        lines.append(f"{emoji} {symbol} ({'Long' if direction=='long' else 'Short'}) → R{max_r} | نتیجه: {result_r:+.1f}R")

    await query.message.reply_text("\n".join(lines), reply_markup=backtest_menu())
    await query.message.reply_document(
        document=excel_buffer, filename=f"backtest_group_{batch_id}.xlsx",
        caption="📎 فایل اکسل نتایج بک‌تست گروهی"
    )

# ═══════════════ پایان MODULE: BACKTEST ════════════════════════



# ╔══════════════════════════════════════════════════════════════╗
# ║                      MAIN MENU                               ║
# ║                                                              ║
# ║  برای اضافه کردن دکمه: یه خط InlineKeyboardButton اضافه کن ║
# ║  برای حذف دکمه: اون خط رو پاک کن                           ║
# ╚══════════════════════════════════════════════════════════════╝

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 بازارهای مالی",      callback_data="menu_financial")],   # ← حذف = پاک کن این خط
        [InlineKeyboardButton("📊 حسابداری شخصی",      callback_data="menu_accounting")],  # ← حذف = پاک کن این خط
        [InlineKeyboardButton("⏰ یادآور و تسک",        callback_data="menu_reminder")],    # ← حذف = پاک کن این خط
        [InlineKeyboardButton("🤖 دستیار هوش مصنوعی",  callback_data="menu_ai")],          # ← حذف = پاک کن این خط
        [InlineKeyboardButton("👶 فرزندان",             callback_data="menu_kids")],        # ← حذف = پاک کن این خط
        [InlineKeyboardButton("🎮 سرگرمی",              callback_data="menu_fun")],         # ← حذف = پاک کن این خط
        [InlineKeyboardButton("📚 یادگیری",             callback_data="menu_learn")],       # ← حذف = پاک کن این خط
        [InlineKeyboardButton("⚙️ تنظیمات",             callback_data="menu_settings")],    # ← حذف = پاک کن این خط
        [InlineKeyboardButton("🌐 ترجمه",  callback_data="menu_translate")],  # ← حذف = پاک کن این خط
    ])


# ╔══════════════════════════════════════════════════════════════╗
# ║                       ROUTER                                 ║
# ║                                                              ║
# ║  هر دکمه به ماژول مربوطه هدایت میشه                        ║
# ║  برای حذف ماژول: خط مربوطه رو پاک کن                       ║
# ╚══════════════════════════════════════════════════════════════╝

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user.id, user.username or "", user.first_name)
    db_user = get_user(user.id)
    clear_state(user.id)

    if db_user[3] == 'active':
        expires = datetime.strptime(db_user[5], "%Y-%m-%d %H:%M")
        if datetime.now() >= expires:
            reject_user(user.id)
            await update.message.reply_text(f"⏳ {user.first_name} عزیز،\nاشتراک شما منقضی شده.")
            await context.bot.send_message(ADMIN_ID, f"🔄 منقضی:\n👤 {user.first_name}\n🆔 {user.id}")
            return
        await update.message.reply_text(f"✅ {user.first_name} عزیز، خوش آمدید!\n\n🏠 منوی اصلی:",
            reply_markup=main_menu())
        return

    if db_user[3] == 'rejected':
        await update.message.reply_text(f"❌ {user.first_name} عزیز،\nدسترسی شما تأیید نشده.")
        return

    await update.message.reply_text(f"👋 سلام {user.first_name} عزیز!\n\n✅ درخواست ثبت شد.\n⏳ منتظر تأیید ادمین باشید.")
    await context.bot.send_message(ADMIN_ID,
        f"🔔 کاربر جدید:\n👤 {user.first_name}\n🆔 {user.id}\n📛 @{user.username or 'ندارد'}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{user.id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject_{user.id}")
        ]]))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    db_user = get_user(user_id)

    # ─── ادمین ───
    if data.startswith("approve_") or data.startswith("reject_"):
        action, uid = data.split("_")
        uid = int(uid)
        target = get_user(uid)
        name = target[2] if target else str(uid)
        if action == "approve":
            approve_user(uid)
            target = get_user(uid)
            await query.edit_message_text(f"✅ {name} تأیید شد.\n📅 انقضا: {target[5]}")
            await context.bot.send_message(uid, f"🎉 {name} عزیز!\n✅ تأیید شدید.\n📅 اشتراک تا {target[5]}\n\n/start بزنید.")
        else:
            reject_user(uid)
            await query.edit_message_text(f"❌ {name} رد شد.")
            await context.bot.send_message(uid, f"❌ {name} عزیز،\nمتأسفانه تأیید نشدید.")
        return

    if not db_user or db_user[3] != 'active':
        await query.answer("❌ دسترسی شما فعال نیست.", show_alert=True)
        return

    # ─── منوی اصلی ───
    if data == "back_main":
        clear_state(user_id)
        await query.edit_message_text("🏠 منوی اصلی:", reply_markup=main_menu())
        return

    # ─── ROUTER: هر prefix به ماژول مربوطه ───
    if data.startswith("ps_"):
        await handle_position_size(query, user_id)  # ← حذف ماژول میزان خرید = پاک کن این خط

    elif data.startswith("bt_") or data == "fin_backtest":
        handled = await handle_backtest_buttons_extra(query, user_id, data)  # ← حذف ماژول بک‌تست = پاک کن این خط
        if not handled:
            await handle_backtest(query, user_id)  # ← حذف ماژول بک‌تست = پاک کن این خط

    elif data.startswith("menu_financial") or data.startswith("fin_") or data.startswith("wl_") or data.startswith("alarm_") or data.startswith("back_financial"):
        await handle_financial(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_accounting") or data.startswith("acc_"):
        await handle_accounting(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_reminder") or data.startswith("rem_"):
        await handle_reminder(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_ai") or data.startswith("ai_"):
        await handle_ai(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_kids") or data.startswith("kids_"):
        await handle_kids(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif (data.startswith("menu_fun") or data == "back_fun" or
          data.startswith("menu_health") or data.startswith("health_") or
          data.startswith("menu_book") or data.startswith("book_") or
          data.startswith("menu_music") or data.startswith("music_")):
        await handle_fun(query, user_id)  # ← حذف ماژول سرگرمی = پاک کن این خط

    elif data.startswith("menu_learn") or data.startswith("learn_"):
        await handle_learning(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_settings") or data.startswith("set_"):
        await handle_settings(query, user_id)  # ← حذف ماژول = پاک کن این خط
    
    elif data.startswith("menu_translate") or data.startswith("tr_"):
        await handle_translate(query, user_id)  # ← حذف ماژول = پاک کن این خط

    else:
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)
 
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_user = get_user(user_id)
    if not db_user or db_user[3] != 'active':
        return

    text = update.message.text.strip()
    state, _ = get_state(user_id)


    if state in ["wl_add", "alarm_symbol", "alarm_price"]:
        await handle_financial_message(user_id, text, update)  # ← حذف ماژول = پاک کن این خط

    elif state in ["tr_waiting"]:
        await handle_translate_message(user_id, text, update)  # ← حذف ماژول = پاک کن این خط

    elif state and (state.startswith("book_") or state.startswith("music_")):
        await handle_fun_message(user_id, text, update)  # ← حذف ماژول سرگرمی = پاک کن این خط

    elif state == "ai_chatting":
        await handle_ai_message(user_id, text, update)  # ← حذف ماژول دستیار AI = پاک کن این خط

    elif state and state.startswith("kids_"):
        await handle_kids_message(user_id, text, update)  # ← حذف ماژول فرزندان = پاک کن این خط

    elif state and state.startswith("ps_"):
        await handle_position_size_message(user_id, text, update)  # ← حذف ماژول میزان خرید = پاک کن این خط

    elif state and state.startswith("bt_"):
        await handle_backtest_message(user_id, text, update)  # ← حذف ماژول بک‌تست = پاک کن این خط

# ╔══════════════════════════════════════════════════════════════╗
# ║                       RUN BOT                                ║
# ╚══════════════════════════════════════════════════════════════╝

async def post_init(app):
    asyncio.create_task(alarm_loop(app))
    print("⏰ سیستم آلارم فعال شد")

# ─── Web server ساده برای Railway (بدون این Railway بات رو خاموش می‌کنه) ───
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, *args):
        pass

def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

Thread(target=run_web, daemon=True).start()

init_db()
init_backtest_db()  # ← حذف ماژول بک‌تست = پاک کن این خط
init_fun_db()  # ← حذف ماژول سرگرمی = پاک کن این خط
init_ai_db()  # ← حذف ماژول دستیار AI = پاک کن این خط
app = Application.builder().token(TOKEN).post_init(post_init).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))  # ← حذف ماژول سرگرمی = پاک کن این خط
print("Bot is running...")
app.run_polling()