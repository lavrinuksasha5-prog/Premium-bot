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
from aiogram.types import Message, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
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
DEVICES_FILE = data_dir / "devices.json"
TDATA_DIR = data_dir / "tdata"
TDATA_DIR.mkdir(exist_ok=True)
TEMP_ZIP = data_dir / "temp.zip"

class UploadStates(StatesGroup):
    waiting_zip = State()
    waiting_proxies = State()

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
        devices[acc_id] = generate_unique_device()
        save_json(DEVICES_FILE, devices)
    return devices[acc_id]

def parse_proxy(proxy_str):
    from python_socks import ProxyType
    proxy_str = proxy_str.strip()
    if " " in proxy_str: proxy_str = proxy_str.split(" ")[-1] 
    if not proxy_str.startswith(('http://', 'https://', 'socks5://', 'socks4://')):
        proxy_str = 'socks5://' + proxy_str
    parsed = urlparse(proxy_str)
    p_type = ProxyType.SOCKS5 if parsed.scheme == 'socks5' else ProxyType.SOCKS4 if parsed.scheme == 'socks4' else ProxyType.HTTP
    return (p_type, parsed.hostname, parsed.port, True, parsed.username, parsed.password), proxy_str, parsed.hostname

def get_ip_country(ip):
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}?fields=status,countryCode", timeout=3)
        if r.json().get("status") == "success": return r.json().get("countryCode")
    except: pass
    return "UNKNOWN"

def find_tdata_folders(base_path):
    folders = []
    for root, dirs, files in os.walk(base_path):
        if "key_d" in files:
            folders.append(root)
    return folders

# --- API ДЛЯ ВЕБ-ПАНЕЛИ ---
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

# --- ВСТРОЕННЫЙ HTML ВЕБ-ПАНЕЛИ ---
async def handle_index(request):
    html_content = """
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Premium Manager</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <style>
            :root { --bg: var(--tg-theme-bg-color, #121212); --card: var(--tg-theme-secondary-bg-color, #1e1e1e); --text: var(--tg-theme-text-color, #fff); --hint: var(--tg-theme-hint-color, #888); --success: #2ecc71; --danger: #e74c3c; }
            body { font-family: -apple-system, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 16px; }
            .card { background: var(--card); border-radius: 16px; padding: 16px; margin-bottom: 12px; border: 1px solid rgba(255,255,255,0.05); }
            .header { font-size: 20px; font-weight: 700; margin-bottom: 16px; text-align: center; }
            .btn { width: 100%; padding: 14px; border: none; border-radius: 12px; font-weight: 700; font-size: 16px; cursor: pointer; margin-top: 10px; }
            .btn-buy { background: var(--success); color: #fff; padding: 8px; font-size: 14px; margin-top: 10px; width: auto; display: inline-block; }
            .account-row { display: flex; flex-direction: column; gap: 8px; border-bottom: 1px solid rgba(255,255,255,0.1); padding: 12px 0; }
            .account-row:last-child { border-bottom: none; }
            .acc-top { display: flex; justify-content: space-between; align-items: center; }
            .acc-name { font-weight: 700; font-size: 16px; }
            .acc-phone { font-size: 14px; color: var(--hint); }
            .acc-details { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px; margin-top: 4px; }
            .detail-item { background: rgba(255,255,255,0.03); padding: 6px; border-radius: 6px; }
            .detail-label { color: var(--hint); font-size: 11px; display: block; }
            .status-badge { padding: 4px 8px; border-radius: 6px; font-size: 12px; font-weight: 700; }
            .prem-yes { background: rgba(46, 204, 113, 0.2); color: var(--success); }
            .prem-no { background: rgba(231, 76, 60, 0.2); color: var(--danger); }
            .total-counter { font-size: 14px; color: var(--hint); text-align: center; margin-bottom: 12px; }
        </style>
    </head>
    <body>
        <div class="header">💎 Premium Manager</div>
        <div class="total-counter" id="total-counter">Загрузка данных...</div>
        <div id="accounts-list"></div>
        <script>
            const tg = window.Telegram.WebApp; tg.expand();
            const API_URL = window.location.origin; 

            async function fetchAccounts() {
                try {
                    const res = await fetch(`${API_URL}/api/accounts`);
                    if (!res.ok) throw new Error("Сервер не отвечает");
                    const accounts = await res.json();
                    const listEl = document.getElementById('accounts-list');
                    const counterEl = document.getElementById('total-counter');
                    if (accounts.length === 0) {
                        counterEl.innerText = "Нет загруженных аккаунтов";
                        listEl.innerHTML = "<div style='text-align:center; color:var(--hint);'>Зайди в бота и загрузи ZIP с аккаунтами.</div>";
                        return;
                    }
                    counterEl.innerText = `Всего аккаунтов: ${accounts.length}`;
                    listEl.innerHTML = accounts.map(acc => {
                        const premClass = acc.premium ? 'prem-yes' : 'prem-no';
                        const premText = acc.premium ? `Premium до ${acc.premium_expires}` : 'Без Premium';
                        return `
                        <div class="card account-row">
                            <div class="acc-top">
                                <div>
                                    <div class="acc-name">${acc.username} ${acc.first_name}</div>
                                    <div class="acc-phone">${acc.phone}</div>
                                </div>
                                <div class="status-badge ${premClass}">${premText}</div>
                            </div>
                            <div class="acc-details">
                                <div class="detail-item"><span class="detail-label">Страна (IP)</span> ${acc.proxy_country} (${acc.ip})</div>
                                <div class="detail-item"><span class="detail-label">Устройство</span> ${acc.device}</div>
                                <div class="detail-item" style="grid-column: span 2;"><span class="detail-label">Прокси</span> ${acc.proxy}</div>
                            </div>
                            <button class="btn btn-buy" onclick="buyPremium('${acc.id}')">💎 Купить Premium (1 мес)</button>
                        </div>
                        `;
                    }).join("");
                } catch (e) { 
                    document.getElementById('total-counter').innerText = "Ошибка загрузки";
                    document.getElementById('accounts-list').innerHTML = `<div style='text-align:center; color:var(--danger);'>${e.message}</div>`;
                }
            }

            async function buyPremium(accountId) {
                try {
                    const res = await fetch(`${API_URL}/api/get_premium_link?account_id=${accountId}`);
                    const data = await res.json();
                    if (data.status === 'ok') {
                        tg.showPopup({
                            title: 'Оплата Premium (1 месяц)',
                            message: `Открой @PremiumBot строго через этот прокси:\\n${data.proxy}\\n\\nВ боте выбери "1 месяц" и оплати картой.`,
                            buttons: [{id: 'open', type: 'default', text: 'Открыть @PremiumBot'}]
                        }, function(btnId) {
                            if (btnId === 'open') { tg.openLink(data.premium_url); }
                        });
                    } else { tg.showAlert("Ошибка: " + data.message); }
                } catch (e) { tg.showAlert("Ошибка: " + e.message); }
            }
            document.addEventListener('DOMContentLoaded', () => { fetchAccounts(); });
        </script>
    </body>
    </html>
    """
    return web.Response(text=html_content, content_type='text/html')

# --- ЛОГИКА БОТА ---
@dp.message(Command("start"))
async def cmd_start(message: Message):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚀 Загрузить аккаунты")]], resize_keyboard=True)
    await message.answer("Привет. Жми кнопку, чтобы загрузить ZIP с TData.", reply_markup=kb)

@dp.message(F.text == "🚀 Загрузить аккаунты")
async def start_work_btn(message: Message, state: FSMContext):
    await state.set_state(UploadStates.waiting_zip)
    await message.answer("Скинь ZIP-архив с папками TData.", reply_markup=ReplyKeyboardRemove())

@dp.message(UploadStates.waiting_zip, F.document)
async def process_zip(message: Message, state: FSMContext):
    if not message.document.file_name.endswith('.zip'): 
        return await message.answer("Это не ZIP файл.")
    
    await state.set_state(UploadStates.waiting_proxies)
    msg = await message.answer("⏳ Скачиваю и распаковываю архив...")
    
    await bot.download(message.document, destination=TEMP_ZIP)
    if TDATA_DIR.exists(): shutil.rmtree(TDATA_DIR)
    TDATA_DIR.mkdir()
    try:
        with zipfile.ZipFile(TEMP_ZIP) as z: z.extractall(TDATA_DIR)
        await msg.edit_text("✅ Архив распакован. Теперь скинь список прокси текстом.")
    except Exception as e: 
        await msg.edit_text(f"❌ Ошибка распаковки: {e}")

@dp.message(UploadStates.waiting_proxies, F.text)
async def process_proxies(message: Message, state: FSMContext):
    await state.set_state(None)
    msg = await message.answer("⏳ Начинаю обработку. Жди...")
    
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.users import GetFullUserRequest
        from tdata_reader import read_tdata
    except ImportError as e:
        return await msg.edit_text(f"❌ Ошибка импорта: {e}")

    proxies_text = message.text.strip()
    proxy_list = []
    for p_raw in proxies_text.split('\n'):
        p_raw = p_raw.strip()
        if not p_raw: continue
        p_tuple, p_str, p_ip = parse_proxy(p_raw)
        proxy_list.append({"raw": p_str, "tuple": p_tuple, "country": get_ip_country(p_ip)})
    
    if not proxy_list:
        return await msg.edit_text("❌ Список прокси пуст. Начни заново /start")
        
    accounts = []
    folders = find_tdata_folders(TDATA_DIR)
    
    if not folders:
        return await msg.edit_text("❌ Не найдены файлы TData (key_d) в архиве. Проверь структуру ZIP.")
    
    for i, folder in enumerate(folders):
        acc_id = os.path.basename(folder) 
        device = get_sticky_device(acc_id)
        
        acc_info = {
            "id": acc_id, "username": "Ошибка", "first_name": "", "phone": "N/A", 
            "country": "Неизвестно", "ip": "N/A", "proxy_country": "UNKNOWN", 
            "time": datetime.now().strftime("%H:%M"), "premium": False, 
            "premium_expires": "Нет", "valid": False, 
            "device": f"{device['model']} ({device['system']})", "proxy": "Нет"
        }
        
        await asyncio.sleep(random.uniform(2.0, 4.0))
        
        try:
            acc = read_tdata(folder)
            session_string = acc.to_string_session()
            temp_proxy = random.choice(proxy_list)
            
            client = TelegramClient(StringSession(session_string), acc.api_id, acc.api_hash, device_model=device['model'], system_version=device['system'], app_version=device['app'], proxy=temp_proxy['tuple'])
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
                    acc_info["premium_expires"] = datetime.fromtimestamp(full_user.full_user.premium_expires).strftime("%d.%m.%Y")
                
                if me.phone: acc_info["country"] = "UA" if me.phone.startswith("380") else "RU" if me.phone.startswith("7") else "US"
                
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
            print(f"ОШИБКА АККАУНТА {acc_id}: {e}")
            acc_info["username"] = f"Ошибка: {str(e)[:30]}"
            
        accounts.append(acc_info)
        save_json(ACCOUNTS_FILE, accounts)
        
    await msg.delete()
    
    if not accounts:
        return await message.answer("❌ Не удалось обработать ни одного аккаунта.")
        
    # Отправляем кнопку для открытия ВЕБ-ПАНЕЛИ
    web_app = WebAppInfo(url="https://worker-production-578b44ee.up.railway.app/")
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="💎 Открыть панель", web_app=web_app)]], resize_keyboard=True)
    
    await message.answer(f"✅ Обработано аккаунтов: {len(accounts)}.\nОткрывай панель для управления:", reply_markup=kb)

async def main():
    app = web.Application(client_max_size=1024**3, middlewares=[cors_middleware])
    app.router.add_get('/api/accounts', handle_get_accounts)
    app.router.add_get('/api/get_premium_link', handle_get_premium_link)
    app.router.add_get('/', handle_index) # Отдаем HTML прямо с Railway
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
