import asyncio
import os
import json
import zipfile
import random
import shutil
import requests
import time
import re
import uuid
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiohttp import web, ClientSession

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
TDATA_DIR = data_dir / "tdata_backup"
TDATA_DIR.mkdir(exist_ok=True)

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

# --- ПАРСЕР ПРОКСИ ---
def format_proxy_url(proxy_str):
    p_str = proxy_str.strip()
    if "://" in p_str: return p_str
    if "@" in p_str: return f"socks5://{p_str}"
    parts = p_str.split(':')
    if len(parts) == 4:
        host, port, login, password = parts
        return f"socks5://{login}:{password}@{host}:{port}"
    return f"socks5://{p_str}"

def check_proxy(proxy_str):
    p_url = format_proxy_url(proxy_str)
    parsed = urlparse(p_url)
    proxy_dict = {"http": p_url, "https": p_url}
    country = "UNKNOWN"
    
    if parsed.username:
        match = re.search(r'country-([A-Z]{2})', parsed.username)
        if match: country = match.group(1)

    start_time = time.time()
    try:
        r = requests.get("http://ip-api.com/json/?fields=status,countryCode,query", 
                         proxies=proxy_dict, timeout=15)
        if r.status_code == 200 and r.json().get("status") == "success":
            ping_ms = int((time.time() - start_time) * 1000)
            real_country = r.json().get("countryCode")
            if real_country: country = real_country
            return True, country, ping_ms
    except Exception as e:
        print(f"Прокси {p_url} не прошел requests: {e}")

    return False, "UNKNOWN", 0

def get_real_ip(proxy_url):
    try:
        r = requests.get("http://ip-api.com/json/?fields=query", proxies={'http': proxy_url, 'https': proxy_url}, timeout=10)
        if r.status_code == 200: return r.json().get("query", "0.0.0.0")
    except: pass
    return "0.0.0.0"

# --- СЛОВАРИ ---
COUNTRY_NAMES = {"RU": "Россия", "UA": "Украина", "IT": "Италия", "DE": "Германия", "FR": "Франция", "US": "США", "GB": "Великобритания", "ES": "Испания", "PL": "Польша", "TR": "Турция", "KZ": "Казахстан", "PT": "Португалия", "UNKNOWN": "Неизвестно"}
FLAGS = {"RU": "🇷🇺", "UA": "🇺🇦", "IT": "🇮🇹", "DE": "🇩🇪", "FR": "🇫🇷", "US": "🇺🇸", "GB": "🇬🇧", "ES": "🇪🇸", "PL": "🇵🇱", "TR": "🇹🇷", "KZ": "🇰🇿", "PT": "🇵🇹", "UNKNOWN": "🏳️"}

def format_phone(phone_str):
    digits = ''.join(filter(str.isdigit, phone_str))
    if not digits: return phone_str
    if len(digits) >= 11: return f"+{digits[0]} {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return f"+{digits}"

# --- БАЗА РЕАЛЬНЫХ УСТРОЙСТВ ---
IOS_MODELS = ["iPhone 11", "iPhone 11 Pro", "iPhone 12", "iPhone 12 Pro", "iPhone 12 Pro Max", "iPhone 13", "iPhone 13 Pro", "iPhone 13 Pro Max", "iPhone 14", "iPhone 14 Plus", "iPhone 14 Pro", "iPhone 14 Pro Max", "iPhone 15", "iPhone 15 Plus", "iPhone 15 Pro", "iPhone 15 Pro Max"]
IOS_VERSIONS = ["15.7.1", "15.8", "16.0", "16.1.1", "16.2", "16.3.1", "16.4", "16.5", "16.6", "16.7.2", "17.0.1", "17.1.1", "17.2", "17.2.1"]
IOS_APP_VERSIONS = ["9.5.1", "9.6.0", "9.6.3", "10.0.0", "10.0.1", "10.1.0", "10.2.0"]

ANDROID_MODELS = ["Samsung Galaxy S21 Ultra", "Samsung Galaxy S22", "Samsung Galaxy S22 Ultra", "Samsung Galaxy S23", "Samsung Galaxy S23 Ultra", "Samsung Galaxy S24", "Xiaomi 11T Pro", "Xiaomi 12", "Xiaomi 13", "Xiaomi 13 Pro", "Xiaomi 14", "Poco F5", "Google Pixel 6", "Google Pixel 6 Pro", "Google Pixel 7", "Google Pixel 7 Pro", "Google Pixel 8", "OnePlus 9 Pro", "OnePlus 10 Pro", "OnePlus 11", "Huawei P40 Pro", "Huawei P50 Pro", "Honor Magic 5 Pro"]
ANDROID_VERSIONS = ["11", "12", "12L", "13", "14"]
ANDROID_APP_VERSIONS = ["9.5.1", "9.6.0", "9.6.3", "10.0.0", "10.0.1", "10.1.0", "10.2.0"]

WIN_OS = ["Windows 10", "Windows 11"]
MAC_OS = ["macOS 12.6", "macOS 13.1", "macOS 13.4", "macOS 14.0", "macOS 14.1"]
DESKTOP_APP_VERSIONS = ["4.8.1", "4.8.10", "4.9.5", "4.10.1", "4.11.2", "4.12.0"]

def generate_unique_device():
    platform = random.choices(["ios", "android", "desktop"], weights=[40, 45, 15], k=1)[0]
    if platform == "ios":
        return {"model": random.choice(IOS_MODELS), "system": f"iOS {random.choice(IOS_VERSIONS)}", "app": random.choice(IOS_APP_VERSIONS), "platform": "ios"}
    elif platform == "android":
        return {"model": random.choice(ANDROID_MODELS), "system": f"Android {random.choice(ANDROID_VERSIONS)}", "app": random.choice(ANDROID_APP_VERSIONS), "platform": "android"}
    else:
        is_mac = random.choice([True, False])
        return {"model": "MacBook Pro" if is_mac else random.choice(["Desktop", "Laptop", "Custom PC"]), "system": random.choice(MAC_OS) if is_mac else random.choice(WIN_OS), "app": random.choice(DESKTOP_APP_VERSIONS), "platform": "mac" if is_mac else "win"}

def get_sticky_device(acc_id):
    devices = load_json(DEVICES_FILE, {})
    if acc_id not in devices or 'platform' not in devices[acc_id]:
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

async def handle_delete_account(request):
    try:
        data = await request.json()
        acc_id = data.get("account_id")
        accounts = load_json(ACCOUNTS_FILE, [])
        accounts = [a for a in accounts if a["id"] != acc_id]
        save_json(ACCOUNTS_FILE, accounts)
        
        tdata_path = TDATA_DIR / acc_id
        if tdata_path.exists(): shutil.rmtree(tdata_path)
        
        return web.json_response({"status": "ok"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def handle_update_proxy(request):
    try:
        data = await request.json()
        acc_id = data.get("account_id")
        new_proxy = data.get("new_proxy")
        
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
            acc["ip"] = get_real_ip(formatted_proxy)
            save_json(ACCOUNTS_FILE, accounts)
            return web.json_response({"status": "ok", "proxy_country": acc["proxy_country"], "proxy_flag": acc["proxy_flag"], "speed": ping, "ip": acc["ip"]})
        return web.json_response({"status": "error", "message": "Аккаунт не найден"}, status=404)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def handle_index(request):
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists(): return web.FileResponse(html_path)
    return web.Response(text="Ошибка: index.html не найден", status=404)

# --- НОВЫЙ ЭНДПОИНТ ДЛЯ ОТКРЫТИЯ В WEB ---
async def handle_open_web(request):
    try:
        data = await request.json()
        acc_id = data.get("account_id")
        
        tdata_path = TDATA_DIR / acc_id
        if not tdata_path.exists():
            return web.json_response({"status": "error", "message": "tdata не найдена"}, status=400)

        # Читаем tdata и достаем auth_key + dc_id
        tdata_acc = read_tdata(str(tdata_path))
        
        # Возвращаем HTML-страницу, которая откроет Telegram Web и вставит ключи
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Открытие Telegram Web...</title>
            <style>body {{ background: #121212; color: #fff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; flex-direction: column; }}</style>
        </head>
        <body>
            <h2>Заходим в аккаунт...</h2>
            <div class="spinner"></div>
            <p id="status">Подготовка...</p>
            <script>
                const authKey = "{tdata_acc.auth_key}"; // Твой ключ из tdata
                const dcId = {tdata_acc.dc_id};
                
                // Открываем Telegram Web
                const win = window.open('https://web.telegram.org/k/', '_blank');
                
                setTimeout(() => {{
                    if (!win) {{
                        document.getElementById('status').innerText = 'Разрешите всплывающие окна для этого сайта!';
                        return;
                    }}
                    try {{
                        // Вставляем ключи в localStorage Telegram Web
                        win.localStorage.setItem(`dc${{dcId}}_auth_key`, `"${{authKey}}"`);
                        win.localStorage.setItem('user_auth', JSON.stringify({{ dcID: dcId, id: "0" }}));
                        win.location.reload(); // Перезагружаем, чтобы подхватил
                        document.getElementById('status').innerText = 'Аккаунт открыт! Можете закрыть эту вкладку.';
                    }} catch(e) {{
                        document.getElementById('status').innerText = 'Ошибка: ' + e.message;
                    }}
                }}, 3000); // Ждем 3 секунды, пока загрузится Telegram Web
            </script>
        </body>
        </html>
        """
        return web.Response(text=html_content, content_type='text/html')

    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

# ... Остальные функции (handle_buy_premium и др.) оставляем как были ...

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_states[message.from_user.id] = None
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 Загрузить Аккаунты")]], resize_keyboard=True)
    await message.answer("Привет. Жми кнопку, чтобы загрузить архив.", reply_markup=kb)

@dp.message(F.text == "🚀 Загрузить Аккаунты")
async def start_work_btn(message: Message):
    user_states[message.from_user.id] = "waiting_zip"
    await message.answer("Скинь ZIP-архив с tdata / session + json.", reply_markup=ReplyKeyboardRemove())

@dp.message(F.document)
async def process_zip(message: Message):
    if user_states.get(message.from_user.id) != "waiting_zip": return
    if not message.document.file_name.endswith('.zip'): return await message.answer("Это не ZIP.")
    
    user_states[message.from_user.id] = "waiting_proxies"
    status_msg = await message.answer("⏳ Скачиваю и распаковываю...")
    
    await bot.download(message.document, destination=TEMP_ZIP)
    if WORK_DIR.exists(): shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir()
    
    try:
        with zipfile.ZipFile(TEMP_ZIP) as z: z.extractall(WORK_DIR)
        await status_msg.edit_text("✅ Распаковано. Теперь скинь прокси (каждый с новой строки).")
    except Exception as e: 
        await status_msg.edit_text(f"❌ Ошибка распаковки: {e}")

@dp.message(F.text)
async def process_proxies(message: Message):
    if user_states.get(message.from_user.id) != "waiting_proxies": return
    user_states[message.from_user.id] = None
    
    status_msg = await message.answer("⏳ Проверяю прокси и конвертирую сессии. Жди...")
    
    raw_proxies = [line.strip() for line in message.text.strip().split('\n') if line.strip()]
    valid_proxies = []
    
    for p in raw_proxies:
        is_alive, country, ping = check_proxy(p)
        if is_alive:
            valid_proxies.append({"raw": format_proxy_url(p), "country": country, "speed": ping})

    if not valid_proxies:
        return await status_msg.edit_text("❌ Все прокси оказались нерабочими.")

    found_accounts = []
    processed_ids = set()

    for root, dirs, files in os.walk(WORK_DIR):
        if "key_datas" in files:
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
                elif phone.startswith("351"): acc_country = "PT"
            
            if acc_country == "UNKNOWN":
                acc_country = valid_proxies[0]["country"]

            best_proxy = valid_proxies[0]
            matching = [p for p in valid_proxies if p["country"] == acc_country]
            if matching: best_proxy = matching[0]

            acc_data = await parse_account_data(root, "tdata", phone, acc_country, best_proxy)
            if acc_data:
                found_accounts.append(acc_data)
                processed_ids.add(phone)

    save_json(ACCOUNTS_FILE, found_accounts)
    
    if not found_accounts:
        await status_msg.edit_text("❌ Аккаунты не найдены или не удалось подключиться.")
        return
        
    web_app = WebAppInfo(url="https://worker-production-578b44ee.up.railway.app/")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="💎 Открыть панель", web_app=web_app)]], resize_keyboard=True)
    
    await status_msg.delete()
    await message.answer(f"✅ Обработано: {len(found_accounts)}.\nОткрывай панель:", reply_markup=kb)

async def parse_account_data(path, acc_type, phone, acc_country, best_proxy):
    device = get_sticky_device(phone)
    
    acc_record = {
        "id": phone, 
        "username": f"Аккаунт {format_phone(phone)}", 
        "phone": format_phone(phone), 
        "account_country": COUNTRY_NAMES.get(acc_country, "Неизвестно"), 
        "flag": FLAGS.get(acc_country, "🏳️"),
        "proxy_country": COUNTRY_NAMES.get(best_proxy["country"], "Неизвестно"),
        "proxy_flag": FLAGS.get(best_proxy["country"], "🏳️"),
        "speed": best_proxy["speed"] + random.randint(1, 15),
        "premium": False, 
        "premium_expires": "Нет", 
        "device": f"{device['model']} ({device['system']})", 
        "device_fp": f"{device['platform']}_{device['app']}", 
        "proxy": best_proxy['raw'],
        "ip": get_real_ip(best_proxy['raw']),
        "status": "⏳ Ожидает"
    }

    try:
        parsed_proxy = urlparse(best_proxy['raw'])
        proxy_dict = {
            'proxy_type': 'socks5',
            'addr': parsed_proxy.hostname,
            'port': parsed_proxy.port,
            'username': parsed_proxy.username,
            'password': parsed_proxy.password,
            'rdns': True
        }

        tdata_backup_path = TDATA_DIR / phone

        if not tdata_backup_path.exists():
            shutil.copytree(path, tdata_backup_path)

        if acc_type == "tdata":
            acc = read_tdata(path)
            session_str = acc.to_string_session()
            client = TelegramClient(
                StringSession(session_str), 
                acc.api_id, acc.api_hash, 
                device_model=device['model'], system_version=device['system'], app_version=device['app'], 
                proxy=proxy_dict
            )
        else:
            return acc_record

        await asyncio.wait_for(client.connect(), timeout=20)
        
        if await client.is_user_authorized():
            me = await client.get_me()
            acc_record["username"] = me.username or f"{me.first_name or ''} {me.last_name or ''}".strip() or f"Без имени"
            
            try:
                full_user = await client(GetFullUserRequest(me.id))
                if hasattr(full_user.full_user, 'premium_expires') and full_user.full_user.premium_expires:
                    acc_record["premium"] = True
                    acc_record["premium_expires"] = datetime.fromtimestamp(full_user.full_user.premium_expires).strftime("%d.%m.%Y")
            except Exception as e:
                print(f"Не удалось проверить премиум {phone}: {e}")
            
            acc_record["status"] = "✅ Подключен"
            await client.disconnect()
        else:
            acc_record["status"] = "❌ Не авторизован"
            await client.disconnect()

    except asyncio.TimeoutError:
        acc_record["status"] = "⚠️ Таймаут прокси"
    except Exception as e:
        acc_record["status"] = f"❌ {str(e)[:30]}"
        print(f"Ошибка {phone}: {e}")

    return acc_record

async def main():
    app = web.Application(client_max_size=1024**3, middlewares=[cors_middleware])
    app.router.add_get('/api/accounts', handle_get_accounts)
    app.router.add_post('/api/update_proxy', handle_update_proxy)
    app.router.add_post('/api/delete_account', handle_delete_account)
    app.router.add_post('/api/open_web', handle_open_web) # Новый эндпоинт
    app.router.add_get('/', handle_index) 
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
