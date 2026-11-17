import os
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command

print("--- ЗАПУСК ТЕСТА ---")

TOKEN = os.getenv("BOT_TOKEN")
print(f"Токен получен: {TOKEN[:10]}..." if TOKEN else "ТОКЕН НЕ НАЙДЕН!")

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: Message):
    print(f"ПОЛУЧЕН /START от {message.from_user.id}")
    await message.answer("Я работаю! Код успешно запущен.")

async def main():
    print("Удаляю вебхук...")
    await bot.delete_webhook(drop_pending_updates=True)
    print("Вебхук удален. Запускаю polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    print("Старт asyncio...")
    asyncio.run(main())
