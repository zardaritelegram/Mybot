

import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# ╔══════════════════════════════════════════════════════════════╗
# ║                    IMPORTS & CONFIG                          ║
# ╚══════════════════════════════════════════════════════════════╝
import httpx
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

TOKEN = "اینجا_توکن_خودت"
ADMIN_ID = اینجا_آیدی_خودت
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
# ╚══════════════════════════════════════════════════════════════╝

def ai_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 شروع چت", callback_data="ai_start")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

async def handle_ai(query, user_id):
    data = query.data
    if data == "menu_ai":
        await query.edit_message_text("🤖 دستیار هوش مصنوعی:", reply_markup=ai_menu())
    else:
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

# ═══════════════ پایان MODULE: AI CHAT ═════════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: KIDS                                ║
# ╚══════════════════════════════════════════════════════════════╝

def kids_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 قصه و سرگرمی", callback_data="kids_story")],
        [InlineKeyboardButton("✏️ تکالیف و مطالعه", callback_data="kids_homework")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

async def handle_kids(query, user_id):
    data = query.data
    if data == "menu_kids":
        await query.edit_message_text("👶 بخش فرزندان:", reply_markup=kids_menu())
    else:
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

# ═══════════════ پایان MODULE: KIDS ════════════════════════════


# ╔══════════════════════════════════════════════════════════════╗
# ║                  MODULE: HEALTH                              ║
# ╚══════════════════════════════════════════════════════════════╝

def health_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚖️ ثبت وزن", callback_data="health_weight")],
        [InlineKeyboardButton("📊 نمودار وزن", callback_data="health_chart")],
        [InlineKeyboardButton("🏃 برنامه ورزشی", callback_data="health_exercise")],
        [InlineKeyboardButton("🔙 برگشت", callback_data="back_main")],
    ])

async def handle_health(query, user_id):
    data = query.data
    if data == "menu_health":
        await query.edit_message_text("🏋️ سلامت و ورزش:", reply_markup=health_menu())
    else:
        await query.answer("🔧 به زودی اضافه می‌شود!", show_alert=True)

# ═══════════════ پایان MODULE: HEALTH ══════════════════════════


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
            ,
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
        [InlineKeyboardButton("🏋️ سلامت و ورزش",       callback_data="menu_health")],      # ← حذف = پاک کن این خط
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
    if data.startswith("menu_financial") or data.startswith("fin_") or data.startswith("wl_") or data.startswith("alarm_") or data.startswith("back_financial"):
        await handle_financial(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_accounting") or data.startswith("acc_"):
        await handle_accounting(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_reminder") or data.startswith("rem_"):
        await handle_reminder(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_ai") or data.startswith("ai_"):
        await handle_ai(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_kids") or data.startswith("kids_"):
        await handle_kids(query, user_id)  # ← حذف ماژول = پاک کن این خط

    elif data.startswith("menu_health") or data.startswith("health_"):
        await handle_health(query, user_id)  # ← حذف ماژول = پاک کن این خط

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
app = Application.builder().token(TOKEN).post_init(post_init).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
print("Bot is running...")
app.run_polling()