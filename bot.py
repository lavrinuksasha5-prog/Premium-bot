import asyncio
import os
import json
import zipfile
import random
import shutil
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiohttp import web

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

def get_ip_country(ip):
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}?fields=status,countryCode", timeout=3)
        if r.json().get("status") == "success": return r.json().get("countryCode")
    except: pass
    return "UNKNOWN"

COUNTRY_NAMES = {
    "RU": "Россия", "UA": "Украина", "IT": "Италия", "DE": "Германия", 
    "FR": "Франция", "US": "США", "GB": "Великобритания", "ES": "Испания",
    "PL": "Польша", "TR": "Турция", "KZ": "Казахстан", "UNKNOWN": "Неизвестно"
}

FLAGS = {"RU": "🇷🇺", "UA": "🇺🇦", "IT": "🇮🇹", "DE": "🇩🇪", "FR": "🇫🇷", "US": "🇺🇸", "GB": "🇬🇧", "ES": "🇪🇸", "PL": "🇵🇱", "TR": "🇹🇷", "KZ": "🇰🇿", "UNKNOWN": "🏳️"}

def get_country_by_phone(phone):
    phone = ''.join(filter(str.isdigit, phone))
    if phone.startswith("380"): return "UA"
    if phone.startswith("7"): return "RU"
    if phone.startswith("39"): return "IT"
    if phone.startswith("49"): return "DE"
    if phone.startswith("33"): return "FR"
    if phone.startswith("90"): return "TR"
    return "UNKNOWN"

def format_phone(phone_str):
    digits = ''.join(filter(str.isdigit, phone_str))
    if not digits: return phone_str
    if digits.startswith("0"): digits = digits[1:]
    if len(digits) >= 11:
        return f"+{digits[0]} ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    elif len(digits) >= 10:
        return f"+{digits[0:2]} {digits[2:5]} {digits[5:8]} {digits[8:10]}"
    elif len(digits) > 5:
        return f"+{digits[0]} {digits[1:]}"
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
    return web.json_response(load_json(ACCOUNTS_FILE, []))

async def handle_index(request):
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists(): return web.FileResponse(html_path)
    return web.Response(text="Ошибка: index.html не найден", status=404)

# --- ОБРАБОТКА КНОПКИ СМЕНЫ ПРОКСИ ---
@dp.message(F.web_app_data)
async def handle_web_app_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        if data.get("action") == "change_proxy":
            acc_id = data.get("acc_id")
            user_states[message.from_user.id] = f"changing_proxy_{acc_id}"
            await message.answer(f"Введи новый прокси для аккаунта {acc_id}:\nФормат: `socks5://user:pass@ip:port`")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

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
    user_id = message.from_user.id
    state = user_states.get(user_id)
    
    # 1. Если это смена прокси
    if state and state.startswith("changing_proxy_"):
        acc_id = state.replace("changing_proxy_", "")
        new_proxy = message.text.strip()
        
        accounts = load_json(ACCOUNTS_FILE, [])
        acc = next((a for a in accounts if a["id"] == acc_id), None)
        
        if acc:
            ip = new_proxy.split('@')[-1].split(':')[0] if '@' in new_proxy else new_proxy.split(':')[0]
            country = get_ip_country(ip)
            
            acc["proxy"] = new_proxy
            acc["proxy_country"] = COUNTRY_NAMES.get(country, "Неизвестно")
            acc["proxy_flag"] = FLAGS.get(country, "🏳️")
            acc["ip"] = ip
            
            save_json(ACCOUNTS_FILE, accounts)
            user_states[user_id] = None
            await message.answer("✅ Прокси успешно изменен! Зайди в панель и обнови.")
        else:
            await message.answer("❌ Аккаунт не найден.")
        return

    # 2. Если это обработка нового архива
    if state != "waiting_proxies": return
    user_states[user_id] = None
    
    status_msg = await message.answer("⏳ Сканирую архив...")
    
    proxies = []
    for line in message.text.strip().split('\n'):
        line = line.strip()
        if not line: continue
        ip = line.split('@')[-1].split(':')[0] if '@' in line else line.split(':')[0]
        country = get_ip_country(ip)
        proxies.append({"raw": line, "country": country, "speed": random.randint(30, 150)})

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
            
            acc_country = get_country_by_phone(phone)
            if acc_country == "UNKNOWN":
                acc_country = proxies[0]["country"] if proxies else "UNKNOWN"
            
            best_proxy = proxies[0] if proxies else {"raw": "Нет", "country": "UNKNOWN", "speed": 0}
            matching = [p for p in proxies if p["country"] == acc_country]
            if matching: best_proxy = matching[0]

            country_name = COUNTRY_NAMES.get(acc_country, "Неизвестно")
            flag = FLAGS.get(acc_country, "🏳️")
            proxy_country_name = COUNTRY_NAMES.get(best_proxy["country"], "Неизвестно")
            proxy_flag = FLAGS.get(best_proxy["country"], "🏳️")

            unique_ip = f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}"
            device = get_sticky_device(phone)
            formatted_phone = format_phone(phone)
            
            found_accounts.append({
                "id": phone, 
                "username": f"Аккаунт {formatted_phone}", 
                "first_name": "", 
                "phone": formatted_phone, 
                "account_country": country_name, 
                "flag": flag,
                "proxy_country": proxy_country_name,
                "proxy_flag": proxy_flag,
                "ip": unique_ip,
                "speed": best_proxy["speed"],
                "premium": False, 
                "premium_expires": "Нет", 
                "device": f"{device['model']} ({device['system']})", 
                "proxy": best_proxy["raw"]
            })
            processed_ids.add(phone)

    save_json(ACCOUNTS_FILE, found_accounts)
    
    if not found_accounts:
        await status_msg.edit_text("❌ Аккаунты не найдены.")
        return
        
    web_app = WebAppInfo(url="https://worker-production-578b44ee.up.railway.app/")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="💎 Открыть панель", web_app=web_app)]], resize_keyboard=True)
    
    await status_msg.edit_text(f"✅ Найдено аккаунтов: {len(found_accounts)}.\nОткрывай панель:", reply_markup=kb)

async def main():
    app = web.Application(client_max_size=1024**3, middlewares=[cors_middleware])
    app.router.add_get('/api/accounts', handle_get_accounts)
    app.router.add_get('/', handle_index) 
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
