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
    return web.Response(text="Ошибка: файл index.html не найден", status=404)

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
    if not message.document.file_name.endswith('.zip'): return await message.answer("Это не ZIP файл.")
    
    user_states[message.from_user.id] = "waiting_proxies"
    
    # Отправляем одно сообщение, которое будем редактировать
    status_msg = await message.answer("⏳ Скачиваю архив...")
    
    await bot.download(message.document, destination=TEMP_ZIP)
    if WORK_DIR.exists(): shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir()
    
    try:
        with zipfile.ZipFile(TEMP_ZIP) as z: z.extractall(WORK_DIR)
        # Меняем текст в том же сообщении
        await status_msg.edit_text("✅ Архив распакован. Теперь скинь список прокси текстом.")
    except Exception as e: 
        await status_msg.edit_text(f"❌ Ошибка распаковки: {e}")

@dp.message(F.text)
async def process_proxies(message: Message):
    if user_states.get(message.from_user.id) != "waiting_proxies": return
    user_states[message.from_user.id] = None
    
    # Отправляем новое сообщение со статусом, которое будем редактировать
    status_msg = await message.answer("⏳ Сканирую архив и подбираю прокси...")
    
    proxies = []
    for line in message.text.strip().split('\n'):
        line = line.strip()
        if not line: continue
        ip = line.split('@')[-1].split(':')[0] if '@' in line else line.split(':')[0]
        country = get_ip_country(ip)
        proxies.append({"raw": line, "country": country, "speed": random.randint(30, 150)})

    found_accounts = []
    
    for root, dirs, files in os.walk(WORK_DIR):
        for file in files:
            if file.endswith(".json"): pass
        if "key_d" in files or "key_datas" in files:
            folder_name = os.path.basename(root)
            
            acc_country = "UNKNOWN"
            if folder_name.startswith("380"): acc_country = "UA"
            elif folder_name.startswith("7"): acc_country = "RU"
            elif folder_name.startswith("39"): acc_country = "IT"
            
            best_proxy = proxies[0] if proxies else {"raw": "Нет", "country": "UNKNOWN", "speed": 0}
            matching = [p for p in proxies if p["country"] == acc_country]
            if matching:
                best_proxy = matching[0]
            
            found_accounts.append({
                "id": folder_name, "username": f"Аккаунт {folder_name}", "first_name": "", "phone": folder_name, 
                "account_country": acc_country, "proxy_country": best_proxy["country"], "speed": best_proxy["speed"],
                "premium": False, "premium_expires": "Нет", "device": "iPhone 13 (TData)", "proxy": best_proxy["raw"]
            })

    save_json(ACCOUNTS_FILE, found_accounts)
    
    if not found_accounts:
        await status_msg.edit_text("❌ Аккаунты не найдены.")
        return
        
    web_app = WebAppInfo(url="https://worker-production-578b44ee.up.railway.app/")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="💎 Открыть панель", web_app=web_app)]], resize_keyboard=True)
    
    # Редактируем то же сообщение на финальный результат
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
