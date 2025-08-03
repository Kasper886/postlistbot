import asyncio
import os
from dotenv import load_dotenv
import html
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from telethon import TelegramClient


# ==== ТВОИ ДАННЫЕ ====
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))
# ======================

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
client = TelegramClient("session", API_ID, API_HASH)

def is_authorized(message: Message) -> bool:
    return message.from_user.id == ALLOWED_USER_ID #Проверяем пользователя, имеющего доступ к боту


def build_report(posts):
    posts.reverse()
    intro = (
        "<b><i>📝 Неделя выдалась насыщенной, поэтому мы публикуем все посты, "
        "которые были опубликованы за это время, чтобы вы могли легче найти то, "
        "что вам действительно интересно.</i></b>\n\n"
    )
    full_text = intro + "\n".join(posts)
    chunks = []
    buffer = ""
    for line in full_text.splitlines(keepends=True):
        if len(buffer) + len(line) > 4000:
            chunks.append(buffer)
            buffer = ""
        buffer += line
    if buffer:
        chunks.append(buffer)
    return chunks


async def collect_posts(date_start: datetime.date, date_end: datetime.date):
    await client.start()
    all_posts = []
    async for msg in client.iter_messages(SOURCE_CHANNEL):
        if not msg.date:
            continue
        msg_date = msg.date.date()
        if msg_date < date_start or msg_date > date_end:
            continue
        text = msg.message or getattr(msg, "text", "") or ""
        text = text.strip()
        if not text:
            continue
        preview = html.escape(text.splitlines()[0])
        link = f"https://t.me/{SOURCE_CHANNEL}/{msg.id}"
        all_posts.append(f"📌 {msg_date.strftime('%d.%m')} — <a href='{link}'>{preview}</a>")
    return all_posts

@dp.message(Command("get_posts"))
async def get_posts(message: Message):

    if not is_authorized(message):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return

    args = message.text.split()
    if len(args) != 3:
        await message.answer("Формат: /get_posts 28.07 03.08")
        return
    try:
        date_start = datetime.strptime(args[1] + ".2025", "%d.%m.%Y").date()
        date_end = datetime.strptime(args[2] + ".2025", "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Неверный формат даты. Используй: 28.07 03.08")
        return

    await message.answer("🔍 Ищу посты...")
    posts = await collect_posts(date_start, date_end)
    if posts:
        for chunk in build_report(posts):
            await message.answer(chunk)
    else:
        await message.answer("Постов не найдено за указанный период.")


@dp.message(Command("schedule_report"))
async def schedule_report(message: Message):

    if not is_authorized(message):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return

    args = message.text.split()
    if len(args) != 4:
        await message.answer("Формат: /schedule_report 03.08.2025 12:00 @канал")
        return
    try:
        run_time = datetime.strptime(args[1] + " " + args[2], "%d.%m.%Y %H:%M")
        channel_username = args[3].lstrip("@")
    except Exception:
        await message.answer("Неверный формат даты или времени.")
        return

    await message.answer(f"✅ Отчёт будет отправлен в @{channel_username} {run_time.strftime('%d.%m.%Y %H:%M')}")
    asyncio.create_task(run_scheduled_report(run_time, channel_username))


async def run_scheduled_report(run_time: datetime, channel: str):
    await asyncio.sleep((run_time - datetime.now()).total_seconds())
    date_end = run_time.date()
    date_start = date_end - timedelta(days=6)
    posts = await collect_posts(date_start, date_end)
    if posts:
        for chunk in build_report(posts):
            await bot.send_message(chat_id=f"@{channel}", text=chunk)
    else:
        await bot.send_message(chat_id=f"@{channel}", text="Постов за неделю не найдено.")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())