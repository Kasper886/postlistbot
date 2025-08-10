import asyncio
import os, re, json
from dotenv import load_dotenv
from telethon.tl.types import Channel
import html
from datetime import datetime, timedelta
from typing import Union, Optional
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from telethon import TelegramClient
from datetime import datetime
import pytz


# ==== ТВОИ ДАННЫЕ ====
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SOURCE_CHANNEL = os.getenv("SOURCE_CHANNEL")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID"))
# ======================

# === ЦЕЛЕВОЙ КАНАЛ ДЛЯ ПУБЛИКАЦИИ ОТЧЁТА ===
# Хранится в файле target_chat.json, при отсутствии берётся из .env (TARGET_CHAT) или SOURCE_CHANNEL.
TARGET_CHAT_FILE = "target_chat.json"
TARGET_CHAT_RUNTIME: Optional[Union[int, str]] = None  # int (-100...) или str('@username')

def parse_chat_ref(s: str) -> Union[int, str]:
    """
    Принимает:
      - @username
      - -1002783609929
      - 2783609929
      - https://t.me/c/2783609929/199
    Возвращает:
      - int для приватных (-100...)
      - str('@username') для публичных
    """
    s = s.strip()
    if s.startswith("@"):
        return s
    m = re.search(r"(?:https?://t\.me/c/)?(?:-100)?(\d{6,})", s)
    if m:
        return int("-100" + m.group(1))
    raise ValueError("Укажите @username или -100…/цифровой ID/ссылку t.me/c/...")

def load_target_chat_from_disk() -> Optional[Union[int, str]]:
    try:
        with open(TARGET_CHAT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("chat")
    except FileNotFoundError:
        return None
    except Exception:
        return None

def save_target_chat_to_disk(value: Union[int, str]) -> None:
    try:
        with open(TARGET_CHAT_FILE, "w", encoding="utf-8") as f:
            json.dump({"chat": value}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # не критично

def init_target_chat():
    """
    Инициализация целевого канала при старте бота:
      1) читаем из файла;
      2) если пусто — из .env TARGET_CHAT;
      3) если пусто — из SOURCE_CHANNEL.
    """
    global TARGET_CHAT_RUNTIME
    v = load_target_chat_from_disk()
    if v is not None:
        TARGET_CHAT_RUNTIME = v
        return
    raw = (os.getenv("TARGET_CHAT") or SOURCE_CHANNEL or "").strip()
    if raw:
        try:
            TARGET_CHAT_RUNTIME = parse_chat_ref(raw)
        except Exception:
            TARGET_CHAT_RUNTIME = None

def get_target_chat() -> Union[int, str]:
    if TARGET_CHAT_RUNTIME is None:
        raise RuntimeError("Целевой канал не задан. Установите его командой /set_target -100… или @username")
    return TARGET_CHAT_RUNTIME
# ============================================


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
        "<b>📝 Неделя выдалась насыщенной, поэтому мы публикуем все посты, "
        "которые были опубликованы за это время, чтобы вы могли легче найти то, "
        "что вам действительно интересно.</b>\n\n"
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

async def resolve_source_entity():
    """
    Возвращает entity канала для Telethon.
    Поддерживает:
    - SOURCE_CHANNEL = -1002746218295 (Bot API формат) -> 2746218295
    - SOURCE_CHANNEL = 2746218295 (внутренний id)
    - SOURCE_CHANNEL = @username (для публичного)
    - SOURCE_CHANNEL = https://t.me/c/2746218295/199 (вытащит 2746218295 и найдёт по диалогам)
    Требование: аккаунт Telethon должен состоять в канале.
    """
    await client.start()
    src = (SOURCE_CHANNEL or "").strip()
    # Попытка по username / прямой ссылке на публичный
    if src.startswith("@") or (src.startswith("https://t.me/") and "/c/" not in src):
        try:
            return await client.get_entity(src)
        except Exception:
            pass  # упадём в поиск по диалогам ниже
    # Достаём цифровой id из -100…, 2746…, или t.me/c/2746…/123
    m = re.search(r'(?:-100)?(\d{6,})', src)  # берём только цифры без -100
    numeric_id = int(m.group(1)) if m else None
    if numeric_id:
        # Ищем среди доступных диалогов, чтобы получить entity c access_hash
        async for d in client.iter_dialogs():
            e = d.entity
            if isinstance(e, Channel) and getattr(e, "id", None) == numeric_id:
                return e
        # Если не нашли — почти наверняка аккаунт не состоит в канале
        raise ValueError(
            f"Аккаунт Telethon не состоит в канале id={numeric_id}. "
            f"Добавьте этот аккаунт в канал (или откройте канал вручную в Telegram под этим аккаунтом), "
            f"чтобы Telethon мог получить access_hash."
        )
    raise ValueError(
        "SOURCE_CHANNEL не распознан. Укажите @username, цифровой ID (без -100) "
        "или ссылку вида https://t.me/c/<id>/<msg> и добавьте аккаунт в канал."
    )

async def collect_posts(date_start: datetime.date, date_end: datetime.date):
    moscow_tz = pytz.timezone("Europe/Moscow")
    await client.start()
    all_posts = []

    # 1) Получаем entity канала
    try:
        entity = await resolve_source_entity()
    except Exception as e:
        print(f"❌ Не удалось получить канал: {e}")
        return []

    # 2) Читаем сообщения
    async for msg in client.iter_messages(entity):
        if not msg.date:
            continue

        msg_date = msg.date.astimezone(moscow_tz).date()
        if not (date_start <= msg_date <= date_end):
            continue

        text = (msg.message or getattr(msg, "text", "") or "").strip()
        if not text:
            continue

        preview = html.escape(text.splitlines()[0])

        # Правильная ссылка на приватный канал: https://t.me/c/<channel_id>/<msg_id>
        # channel_id можно взять из peer_id.channel_id, иначе из entity.id
        channel_id = None
        if getattr(msg, "peer_id", None) and getattr(msg.peer_id, "channel_id", None):
            channel_id = msg.peer_id.channel_id
        else:
            channel_id = getattr(entity, "id", None)

        link = f"https://t.me/c/{channel_id}/{msg.id}"
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

    parts = message.text.split(maxsplit=2)
    if len(parts) == 1:
        await message.answer(
            "Форматы:\n"
            "- /schedule_report now — опубликовать сейчас\n"
            "- /schedule_report 03.08.2025 12:00 — опубликовать в указанное время"
        )
        return

    moscow_tz = pytz.timezone("Europe/Moscow")

    # Мгновенная публикация
    if len(parts) == 2 and parts[1].lower() in ("now", "сейчас"):
        try:
            target_chat = get_target_chat()
        except Exception as e:
            await message.answer(f"⚠️ {e}")
            return

        run_time = moscow_tz.localize(datetime.now())
        await message.answer("⏱ Публикую отчёт сейчас…")
        await run_scheduled_report(run_time, target_chat)
        return

    # Публикация по времени
    if len(parts) == 3:
        date_str, time_str = parts[1], parts[2]
        try:
            run_time = moscow_tz.localize(datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M"))
            target_chat = get_target_chat()
        except ValueError:
            await message.answer("Неверный формат даты/времени. Пример: /schedule_report 03.08.2025 12:00")
            return
        except Exception as e:
            await message.answer(f"⚠️ {e}")
            return

        await message.answer(f"✅ Отчёт будет отправлен {run_time.strftime('%d.%m.%Y %H:%M')}")
        asyncio.create_task(run_scheduled_report(run_time, target_chat))
        return

    await message.answer(
        "Неверный формат. Используйте:\n"
        "- /schedule_report now\n"
        "- /schedule_report ДД.ММ.ГГГГ ЧЧ:ММ"
    )

@dp.message(Command("set_target"))
async def set_target(message: Message):
    if not is_authorized(message):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "Укажите канал/чат для публикации:\n"
            "- /set_target -1002783609929\n"
            "- /set_target @username\n"
            "- /set_target https://t.me/c/2783609929/199"
        )
        return

    raw = parts[1].strip()
    try:
        new_target = parse_chat_ref(raw)
    except Exception as e:
        await message.answer(f"⚠️ Некорректный идентификатор: {e}")
        return

    # Проверим доступность: бот должен быть добавлен в канал и иметь право публиковать
    try:
        chat = await bot.get_chat(new_target)
    except Exception as e:
        await message.answer(
            "⚠️ Не удалось получить доступ к чату/каналу. "
            "Убедитесь, что бот добавлен в канал и имеет право публиковать. "
            f"Детали: {e}"
        )
        return

    # Сохраняем выбор
    global TARGET_CHAT_RUNTIME
    TARGET_CHAT_RUNTIME = new_target
    save_target_chat_to_disk(new_target)

    title = getattr(chat, "title", None) or getattr(chat, "full_name", "чат")
    await message.answer(f"✅ Целевой канал установлен: {title} (id={new_target})")


async def run_scheduled_report(run_time: datetime, target_chat: Union[int, str]):
    from datetime import timezone
    utc_now = datetime.now(timezone.utc)
    sleep_duration = (run_time.astimezone(timezone.utc) - utc_now).total_seconds()
    if sleep_duration > 0:
        await asyncio.sleep(sleep_duration)

    date_end = run_time.date()
    date_start = date_end - timedelta(days=6)

    posts = await collect_posts(date_start, date_end)

    if posts:
        for chunk in build_report(posts):
            await bot.send_message(chat_id=target_chat, text=chunk, disable_web_page_preview=True)
    else:
        await bot.send_message(chat_id=target_chat, text="Постов за неделю не найдено.", disable_web_page_preview=True)


async def main():
    init_target_chat()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
