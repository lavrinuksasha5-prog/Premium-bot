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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", 8080))

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

data_dir = Path("/app/data")
data_dir.mkdir(parents=True, exist_ok=True)
ACCOUNTS_FILE = data_dir / "accounts.json"
WORK_DIR = data_dir / "work"
WORK_DIR.mkdir(exist_ok=True)
TEMP_ZIP = data_dir / "temp.zip"

class UploadStates(StatesGroup):
    waiting_zip = State()
    waiting_proxies = State()

def load_json(file_path, default):
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return default
    return default

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

@web.middleware
async def cors_middleware(request, handler):
    if request.method == 'OPTIONS':
        return web.Response(status=200, headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type"})
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

async def handle_get_accounts(request):
    return web.json_response(load_json(ACCOUNTS_FILE, []))

async def handle_get_premium_link(request):
    acc_id = request.query.get('account_id')
    accounts = load_json(ACCOUNTS_FILE, [])
    acc = next((a for a in accounts if a["id"] == acc_id), None)
    if not acc: return web.json_response({"status": "error", "message": "Не найден"}, status=404)
    return web.json_response({"status": "ok", "premium_url": "https://t.me/PremiumBot", "proxy": acc.get("proxy", "Нет"), "country": acc.get("proxy_country", "UNKNOWN")})

async def handle_index(request):
    html_path = Path(__file__).parent / "index.html"
    if html_path.exists(): return web.FileResponse(html_path)
    return web.Response(text="Ошибка: файл index.html не найден на сервере", status=404)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 Загрузить TData")]], resize_keyboard=True)
    await message.answer("Привет. Жми кнопку, чтобы загрузить ZIP.", reply_markup=kb)

@dp.message(F.text == "🚀 Загрузить TData")
async def start_work_btn(message: Message, state: FSMContext):
    await state.set_state(UploadStates.waiting_zip)
    await message.answer("Скинь ZIP-архив.", reply_markup=ReplyKeyboardRemove())

@dp.message(UploadStates.waiting_zip, F.document)
async def process_zip(message: Message, state: FSMContext):
    if not message.document.file_name.endswith('.zip'): 
        return await message.answer("Это не ZIP файл.")
    
    await state.set_state(UploadStates.waiting_proxies)
    msg = await message.answer("⏳ Скачиваю архив...")
    
    await bot.download(message.document, destination=TEMP_ZIP)
    if WORK_DIR.exists(): shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir()
    
    try:
        with zipfile.ZipFile(TEMP_ZIP) as z: z.extractall(WORK_DIR)
        await msg.edit_text("✅ Архив распакован. Теперь скинь список прокси текстом.")
    except Exception as e: 
        await msg.edit_text(f"❌ Ошибка распаковки: {e}")

@dp.message(UploadStates.waiting_proxies, F.text)
async def process_proxies(message: Message, state: FSMContext):
    await state.clear()
    msg = await message.answer("⏳ Начинаю сканировать архив. Ищу любые сессии...")
    
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.users import GetFullUserRequest
        from tdata_reader import read_tdata
    except ImportError as e:
        return await msg.edit_text(f"❌ Ошибка импорта библиотек: {e}")

    found_accounts = []
    processed = set()

    # 1. ИЩЕМ JSON-ФАЙЛЫ
    for root, dirs, files in os.walk(WORK_DIR):
        for file in files:
            if file.endswith(".json"):
                json_path = os.path.join(root, file)
                acc_id = file.replace(".json", "")
                if acc_id in processed: continue
                try:
                    with open(json_path, "r", encoding="utf-8") as f: data = json.load(f)
                    session_str = data.get("session_string") or data.get("session") or data.get("StringSession")
                    api_id = data.get("api_id") or data.get("app_id")
                    api_hash = data.get("api_hash") or data.get("app_hash")
                    
                    if session_str and api_id and api_hash:
                        found_accounts.append({"id": acc_id, "username": f"JSON: {acc_id}", "first_name": "", "phone": "N/A", "country": "Неизвестно", "ip": "N/A", "proxy_country": "UNKNOWN", "premium": False, "premium_expires": "Нет", "device": "JSON Сессия", "proxy": message.text.strip()[:20]})
                        processed.add(acc_id)
                except:
                    pass

    # 2. ИЩЕМ ПАПКИ TDATA (с key_d или key_datas)
    for root, dirs, files in os.walk(WORK_DIR):
        if "key_d" in files or "key_datas" in files:
            folder_name = os.path.basename(root)
            if folder_name in processed: continue
            try:
                # Пробуем прочитать через tdata_reader
                acc = read_tdata(root)
                # Если получилось, добавляем
                found_accounts.append({"id": folder_name, "username": f"TData: {folder_name}", "first_name": "", "phone": "N/A", "country": "Неизвестно", "ip": "N/A", "proxy_country": "UNKNOWN", "premium": False, "premium_expires": "Нет", "device": "TData Сессия", "proxy": message.text.strip()[:20]})
                processed.add(folder_name)
            except Exception as e:
                print(f"Не удалось прочитать TData {folder_name}: {e}")

    save_json(ACCOUNTS_FILE, found_accounts)
    
    if not found_accounts:
        await msg.edit_text("❌ В архиве не найдено ни JSON с сессиями, ни читаемых папок TData.")
        return
        
    web_app = WebAppInfo(url="https://worker-production-578b44ee.up.railway.app/")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="💎 Открыть панель", web_app=web_app)]], resize_keyboard=True)
    
    await msg.edit_text(f"✅ Найдено аккаунтов: {len(found_accounts)}.\nОткрывай панель:", reply_markup=kb)

async def main():
    app = web.Application(client_max_size=1024**3, middlewares=[cors_middleware])
    app.router.add_get('/api/accounts', handle_get_accounts)
    app.router.add_get('/api/get_premium_link', handle_get_premium_link)
    app.router.add_get('/', handle_index) 
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
