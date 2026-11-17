import asyncio
import os
import json
import zipfile
import random
import shutil
import requests
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiohttp import web

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.users import GetFullUserRequest
from tdata_reader import read_tdata
from python_socks import ProxyType
from python_socks.sync import Proxy

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher()

data_dir = Path("/app/data")
data_dir.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = data_dir / "accounts.json"
DEVICES_FILE = data_dir / "devices.json"
WORK_DIR = data_dir / "work"
WORK_DIR.mkdir(exist_ok=True)
TEMP_ZIP = data_dir / "temp.zip"

user_states = {}

def load_json(file_path, default):
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return default
    return default

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

# --- ФОРМАТИРОВАНИЕ ПРОКСИ ---
def format_proxy_url(proxy_str):
    p_str = proxy_str.strip()
    if "://" in p_str:
        return p_str
    parts = p_str.split(':')
    if len(parts) == 4:
        host, port, login, password = parts
        return f"socks5://{login}:{password}@{host}:{port}"
    return f"socks5://{p_str}"

def check_proxy(proxy_str):
    p_url = format_proxy_url(proxy_str)
    parsed = urlparse(p_url)
    host = parsed.hostname
    port = parsed.port
    login = parsed.username
    password = parsed.password
    
    start_time = time.time()
    try:
        # Проверяем подключение к Telegram (149.154.167.51:443)
        proxy = Proxy.create(
            proxy_type=ProxyType.SOCKS5 if parsed.scheme == 'socks5' else ProxyType.HTTP,
            host=host, port=port, username=login, password=password
        )
        conn = proxy.connect(dest_host='149.154.167.51', dest_port=443, timeout=5)
        conn.close()
        ping_ms = int((time.time() - start_time) * 1000)
        
        # Узнаем страну
        country = "UNKNOWN"
        try:
            r = requests.get("http://ip-api.com/json/?fields=status,countryCode", 
                             proxies={'http': p_url, 'https': p_url}, timeout=3)
            if r.status_code == 200 and r.json().get("status") == "success":
                country = r.json().get("countryCode")
        except: pass
            
        return True, country, ping_ms
    except Exception as e:
        print(f"Прокси не прошел проверку: {e}")
        return False, "UNKNOWN", 0

# --- ДАННЫЕ СТРАН ---
COUNTRY_NAMES = {
    "RU": "Россия", "UA": "Украина", "IT": "Италия", "DE": "Германия", 
    "FR": "Франция", "US": "США", "GB": "Великобритания", "ES": "Испания",
    "PL": "Польша", "TR": "Турция", "KZ": "Казахстан", "UNKNOWN": "Неизвестно"
}

FLAGS = {"RU": "🇷🇺", "UA": "🇺🇦", "IT": "🇮🇹", "DE": "🇩🇪", "FR": "🇫🇷", "US": "🇺🇸", "GB": "🇬🇧", "ES": "🇪🇸", "PL": "🇵🇱", "TR": "🇹🇷", "KZ": "🇰🇿", "UNKNOWN": "🏳️"}

def format_phone(phone_str):
    digits = ''.join(filter(str.isdigit, phone_str))
    if not digits: return phone_str
    if len(digits) >= 11:
        return f"+{digits[0]} {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    elif len(digits) >= 10:
        return f"+{digits[0:2]} {digits[2:5]} {digits[5:8]} {digits[8:10]}"
    else:
        return f"+{digits}"

IPHONE_MODELS = ["iPhone 14 Pro", "iPhone 13", "iPhone 12", "iPhone 15"]
ANDROID_MODELS = ["Samsung Galaxy S23", "Xiaomi 13 Pro", "Google Pixel 7"]
MAC_MODELS = ["MacBook Pro M2", "MacBook Air M1"]
WIN_MODELS = ["Desktop", "Laptop"]
APP_VERSIONS = ["9.6.0", "9.7.0", "10.0.0", "4.8.1"]

def generate_unique_device():
    platform = random.choice(["ios", "android", "mac", "win"])
    if platform == "ios":
        return {"model": random.choice(IPHONE_MODELS), "system": f"iOS {random.choice(['16.0', '16.1.1', '17.0'])}", "app": random.choice(APP_VERSIONS)}
    elif platform == "android":
        return {"model": random.choice(ANDROID_MODELS), "system": f"Android {random.choice(['12', '13', '14'])}", "app": random.choice(APP_VERSIONS)}
    elif platform == "mac":
        return {"model": random.choice(MAC_MODELS), "system": f"macOS {random.choice(['13.1', '14.0'])}", "app": random.choice(APP_VERSIONS)}
    return {"model": random.choice(WIN_MODELS), "system": random.choice(["Windows 10", "Windows 11"]), "app": random.choice(APP_VERSIONS)}

def get_sticky_device(acc_id):
    devices = load_json(DEVICES_FILE, {})
    if acc_id not in devices:
        devices[acc_id] = generate_unique_device()
        save_json(DEVICES_FILE, devices)
    return devices[acc_id]

@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        return web.Response(status=200, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"})
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

async def handle_get_accounts(request):
    response = web.json_response(load_json(ACCOUNTS_FILE, []))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

async def handle_update_proxy(request):
    try:
        data = await request.json()
        acc_id = data.get("account_id")
        new_proxy = data.get("new_proxy")
        
        # Проверяем новый прокси
        is_alive, country, ping = check_proxy(new_proxy)
        if not is_alive:
            return web.json_response({"status": "error", "message": "Прокси не работает"}, status=400)

        accounts = load_json(ACCOUNTS_FILE, [])
        acc = next((a for a in accounts if a["id"] == acc_id), None)
        
        if acc:
            formatted_proxy = format_proxy_url(new_proxy)
            acc["proxy"] = formatted_proxy
            acc["proxy_country"] = COUNTRY_NAMES.get(country, "Неизвестно")
            acc["proxy_flag"] = FLAGS.get(country, "🏳️")
            acc["speed"] = ping
            
            save_json(ACCOUNTS_FILE, accounts)
            return web.json_response({
                "status": "ok",
                "proxy_country": acc["proxy_country"],
                "proxy_flag": acc["proxy_flag"],
                "speed": ping
            })
        return web.json_response({"status": "error", "message": "Аккаунт не найден"}, status=404)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def handle_index(request):
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists(): return web.FileResponse(html_path)
    return web.Response(text="Ошибка: index.html не найден", status=404)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_states[message.from_user.id] = None
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 Загрузить TData")]], resize_keyboard=True)
    await message.answer("Привет. Жми кнопку, чтобы загрузить ZIP.", reply_markup=kb)

@dp.message(F.text == "🚀 Загрузить TData")
async def start_work_btn(message: Message):
    user_states[message.from_user.id] = "waiting_zip"
    await message.answer("Скинь ZIP-архив.", reply_markup=ReplyKeyboardRemove())

@dp.message(F.document)
async def process_zip(message: Message):
    if user_states.get(message.from_user.id) != "waiting_zip": return
    if not message.document.file_name.endswith('.zip'): return await message.answer("Это не ZIP.")
    
    user_states[message.from_user.id] = "waiting_proxies"
    status_msg = await message.answer("⏳ Скачиваю архив...")
    
    await bot.download(message.document, destination=TEMP_ZIP)
    if WORK_DIR.exists(): shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir()
    
    try:
        with zipfile.ZipFile(TEMP_ZIP) as z: z.extractall(WORK_DIR)
        await status_msg.edit_text("✅ Архив распакован. Теперь скинь прокси.")
    except Exception as e: 
        await status_msg.edit_text(f"❌ Ошибка: {e}")

@dp.message(F.text)
async def process_proxies(message: Message):
    if user_states.get(message.from_user.id) != "waiting_proxies": return
    user_states[message.from_user.id] = None
    
    status_msg = await message.answer("⏳ Проверяю прокси и подключаюсь...")
    
    raw_proxies = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    
    valid_proxies = []
    for p in raw_proxies:
        is_alive, country, ping = check_proxy(p)
        if is_alive:
            valid_proxies.append({"raw": format_proxy_url(p), "country": country, "speed": ping})
        else:
            print(f"Прокси {p} отброшен (мертв)")

    if not valid_proxies:
        return await status_msg.edit_text("❌ Все прокси оказались нерабочими.")

    found_accounts = []
    processed_ids = set()

    for root, dirs, files in os.walk(WORK_DIR):
        if "key_d" in files or "key_datas" in files:
            path_parts = root.split(os.sep)
            
            phone = "UNKNOWN"
            for part in reversed(path_parts):
                if part.isdigit() and len(part) > 7: 
                    phone = part
                    break
                elif part not in ["tdata", "work", "data", "app"]:
                    if phone == "UNKNOWN": phone = part

            if phone in processed_ids: continue
            
            acc_country = "UNKNOWN"
            if phone.isdigit():
                if phone.startswith("90"): acc_country = "TR"
                elif phone.startswith("380"): acc_country = "UA"
                elif phone.startswith("39"): acc_country = "IT"
                elif phone.startswith("49"): acc_country = "DE"
            
            if acc_country == "UNKNOWN":
                acc_country = valid_proxies[0]["country"]

            best_proxy = valid_proxies[0]
            matching = [p for p in valid_proxies if p["country"] == acc_country]
            if matching: best_proxy = matching[0]

            username = f"Аккаунт {format_phone(phone)}"
            premium = False
            premium_expires = "Нет"
            status = "❌ Ошибка"

            try:
                acc = read_tdata(root)
                session_string = acc.to_string_session()
                
                parsed = urlparse(best_proxy["raw"])
                p_type = ProxyType.SOCKS5 if parsed.scheme == 'socks5' else ProxyType.SOCKS4 if parsed.scheme == 'socks4' else ProxyType.HTTP
                proxy_tuple = (p_type, parsed.hostname, parsed.port, True, parsed.username, parsed.password)
                
                device = get_sticky_device(phone)
                
                client = TelegramClient(
                    StringSession(session_string), acc.api_id, acc.api_hash, 
                    device_model=device['model'], system_version=device['system'], 
                    app_version=device['app'], proxy=proxy_tuple
                )
                
                await client.connect()
                
                if await client.is_user_authorized():
                    me = await client.get_me()
                    username = me.username or me.first_name or f"Без имени"
                    
                    full_user = await client(GetFullUserRequest(me.id))
                    if full_user.full_user.premium_expires:
                        premium = True
                        premium_expires = datetime.fromtimestamp(full_user.full_user.premium_expires).strftime("%d.%m.%Y")
                    
                    status = "✅ Подключен"
                
                await client.disconnect()
            except Exception as e:
                print(f"Ошибка аккаунта {phone}: {e}")
                status = f"❌ {str(e)[:20]}"

            country_name = COUNTRY_NAMES.get(acc_country, "Неизвестно")
            flag = FLAGS.get(acc_country, "🏳️")
            proxy_country_name = COUNTRY_NAMES.get(best_proxy["country"], "Неизвестно")
            proxy_flag = FLAGS.get(best_proxy["country"], "🏳️")
            device = get_sticky_device(phone)
            
            found_accounts.append({
                "id": phone, 
                "username": username, 
                "phone": format_phone(phone), 
                "account_country": country_name, 
                "flag": flag,
                "proxy_country": proxy_country_name,
                "proxy_flag": proxy_flag,
                "speed": best_proxy["speed"],
                "premium": premium, 
                "premium_expires": premium_expires, 
                "device": f"{device['model']} ({device['system']})", 
                "proxy": best_proxy["raw"],
                "status": status
            })
            processed_ids.add(phone)

    save_json(ACCOUNTS_FILE, found_accounts)
    
    if not found_accounts:
        await status_msg.edit_text("❌ Аккаунты не найдены.")
        return
        
    web_app = WebAppInfo(url="https://worker-production-578b44ee.up.railway.app/")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="💎 Открыть панель", web_app=web_app)]], resize_keyboard=True)
    
    await status_msg.edit_text(f"✅ Обработано: {len(found_accounts)}.\nОткрывай панель:", reply_markup=kb)

async def main():
    app = web.Application(client_max_size=1024**3, middlewares=[cors_middleware])
    app.router.add_get('/api/accounts', handle_get_accounts)
    app.router.add_post('/api/update_proxy', handle_update_proxy)
    app.router.add_get('/', handle_index) 
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
