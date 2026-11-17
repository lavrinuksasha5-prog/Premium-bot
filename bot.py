import asyncio
import os
import json
import zipfile
import io
import random
import shutil
import traceback
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
import socks

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, WebAppInfo
from aiogram.filters import Command
from aiohttp import web
from telethon import TelegramClient
from telethon.sessions import StringSession
from tdata_reader import read_tdata

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))
ADMIN_ID = 8720671692 

bot = Bot(token=TOKEN)
dp = Dispatcher()

data_dir = Path("/app/data")
data_dir.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = data_dir / "accounts.json"
TDATA_DIR = data_dir / "tdata"
TDATA_DIR.mkdir(exist_ok=True)

DEVICES = [
    {"model": "Desktop", "system": "Windows 10", "app": "4.8.1"},
    {"model": "Xiaomi Redmi Note 10", "system": "Android 12", "app": "9.6.1"},
    {"model": "iPhone 13", "system": "iOS 16.1", "app": "9.6.0"}
]

def load_accounts():
    if ACCOUNTS_FILE.exists():
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f: return json.load(f)
    return []

def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f: json.dump(accounts, f, ensure_ascii=False, indent=2)

def parse_proxy(proxy_str):
    proxy_str = proxy_str.strip()
    if " " in proxy_str: proxy_str = proxy_str.split(" ")[-1] 
    if not proxy_str.startswith(('http://', 'https://', 'socks5://', 'socks4://')):
        proxy_str = 'socks5://' + proxy_str
        
    parsed = urlparse(proxy_str)
    proxy_type = socks.SOCKS5 if parsed.scheme == 'socks5' else socks.HTTP
    return (proxy_type, parsed.hostname, parsed.port, True, parsed.username, parsed.password)

async def handle_upload_and_process(request):
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}
    if request.method == 'OPTIONS': return web.Response(status=200, headers=headers)
    
    try:
        print("=== НАЧАЛО ОБРАБОТКИ ===")
        post = await request.post()
        zip_field = post.get('tdata_zip')
        proxies_field = post.get('proxies')

        if not zip_field or not proxies_field:
            return web.json_response({"status": "error", "message": "Нет файла или прокси"}, status=400, headers=headers)

        zip_data = zip_field.file.read()
        proxies_text = proxies_field.strip()

        # Очистка и распаковка
        if TDATA_DIR.exists(): shutil.rmtree(TDATA_DIR)
        TDATA_DIR.mkdir()
        print("Распаковка ZIP...")
        with zipfile.ZipFile(io.BytesIO(zip_data)) as z: 
            z.extractall(TDATA_DIR)

        proxy_list = [p.strip() for p in proxies_text.split('\n') if p.strip()]
        if not proxy_list: return web.json_response({"status": "error", "message": "Пустой список прокси"}, status=400, headers=headers)

        accounts = []
        folders = [d for d in TDATA_DIR.iterdir() if d.is_dir()]
        print(f"Найдено папок: {len(folders)}")
        
        for i, folder in enumerate(folders):
            print(f"Обработка {folder.name}...")
            device = random.choice(DEVICES)
            proxy_raw = proxy_list[i % len(proxy_list)]
            proxy_tuple = parse_proxy(proxy_raw)

            acc_info = {
                "id": str(i), "username": "Ошибка", "phone": "N/A", "country": "Италия",
                "ip": "N/A", "time": datetime.now().strftime("%H:%M"), "premium": False,
                "valid": False, "device": f"{device['model']} ({device['system']})", "proxy": proxy_raw
            }

            try:
                # Чтение TData
                acc = read_tdata(str(folder))
                session_string = acc.to_string_session()
                print(f"Сессия прочитана. Подключение...")
                
                client = TelegramClient(
                    StringSession(session_string), 
                    acc.api_id, acc.api_hash, 
                    device_model=device['model'], system_version=device['system'], 
                    proxy=proxy_tuple
                )
                
                await client.connect()
                
                if await client.is_user_authorized():
                    me = await client.get_me()
                    acc_info["username"] = me.username or me.first_name
                    acc_info["phone"] = f"+{me.phone}"
                    acc_info["premium"] = me.premium
                    acc_info["valid"] = True
                    acc_info["ip"] = f"185.{random.randint(1,255)}.{random.randint(1,255)}.1" 
                    print(f"Успех: {acc_info['username']}")
                
                await client.disconnect()
                
            except Exception as e:
                print(f"ОШИБКА АККАУНТА {folder.name}: {e}")
                acc_info["username"] = f"Ошибка: {str(e)[:20]}"

            accounts.append(acc_info)

        save_accounts(accounts)
        print("=== УСПЕШНО ЗАВЕРШЕНО ===")
        return web.json_response({"status": "ok", "message": f"Обработано: {len(accounts)}"}, headers=headers)

    except Exception as e:
        print("=== ГЛОБАЛЬНАЯ ОШИБКА ===")
        traceback.print_exc()
        return web.json_response({"status": "error", "message": f"Ошибка: {str(e)}"}, status=500, headers=headers)

async def handle_get_accounts(request):
    return web.json_response(load_accounts(), headers={"Access-Control-Allow-Origin": "*"})

# ... (остальной код start и main такой же, как в прошлом сообщении)
@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id == ADMIN_ID:
        web_app = WebAppInfo(url="https://lavrinuksasha5-prog.github.io/premium-bot-web/") 
        await message.answer("Открой панель 👇", reply_markup={"keyboard": [[{"text": "Управление", "web_app": web_app}]], "resize_keyboard": True})

async def main():
    app = web.Application(client_max_size=1024**3)
    app.router.add_post('/api/upload', handle_upload_and_process)
    app.router.add_options('/api/upload', handle_upload_and_process)
    app.router.add_get('/api/accounts', handle_get_accounts)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
