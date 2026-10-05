"""
Jopa Менеджер — телеграм-бот модератор.
Всё в одном файле. SQLite для хранения данных.
"""
import os
import re
import json
import time
import random
import asyncio
import logging
from datetime import datetime, timedelta, date

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.types import (
    Message, ChatPermissions, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart, Command, BaseFilter
from aiogram.enums import ChatMemberStatus
from aiogram.client.default import DefaultBotProperties

import aiosqlite

# =============================================================
#                       КОНФИГ
# =============================================================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@jopmenjer")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/jopmenjer")
DB_PATH = os.getenv("DB_PATH", "jopa.db")

WARN_LIMIT = 3
WARN_MUTE_HOURS = 1

FLOOD_LIMIT = 5
FLOOD_WINDOW = 5
FLOOD_MUTE_MINUTES = 5

CAPS_MIN_LEN = 10
CAPS_MIN_PERCENT = 70

AUTODELETE_SECONDS = 15
SUB_RECHECK_INTERVAL = 3600

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("jopa")


# =============================================================
#                       БАЗА ДАННЫХ
# =============================================================
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
    return _db


async def init_db():
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS warns (
            chat_id INTEGER, user_id INTEGER, count INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS bans (
            chat_id INTEGER, user_id INTEGER, until_ts INTEGER,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id INTEGER PRIMARY KEY,
            antimat INTEGER DEFAULT 0,
            antiflood INTEGER DEFAULT 0,
            antilink INTEGER DEFAULT 0,
            anticaps INTEGER DEFAULT 0,
            welcome INTEGER DEFAULT 1,
            goodbye INTEGER DEFAULT 1,
            rules TEXT,
            welcome_text TEXT,
            goodbye_text TEXT,
            slowmode INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS custom_bad_words (
            chat_id INTEGER, word TEXT,
            PRIMARY KEY (chat_id, word)
        );
        CREATE TABLE IF NOT EXISTS white_list (
            chat_id INTEGER, user_id INTEGER,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS known_users (
            chat_id INTEGER, username TEXT, user_id INTEGER,
            PRIMARY KEY (chat_id, username)
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY, checked_ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS saved_perms (
            chat_id INTEGER, user_id INTEGER, perms TEXT,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS notes (
            chat_id INTEGER, user_id INTEGER, note TEXT, created_ts INTEGER,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS activity (
            chat_id INTEGER, user_id INTEGER, d TEXT, count INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, d)
        );
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER, from_id INTEGER, about_id INTEGER,
            message_id INTEGER, text TEXT, ts INTEGER
        );
    """)
    await db.commit()


async def close_db():
    global _db
    if _db is not None:
        await _db.close()
        _db = None


# ---------------- WARNS ----------------
async def add_warn(chat_id: int, user_id: int) -> int:
    db = await get_db()
    await db.execute(
        "INSERT INTO warns (chat_id,user_id,count) VALUES (?,?,1) "
        "ON CONFLICT(chat_id,user_id) DO UPDATE SET count=count+1",
        (chat_id, user_id),
    )
    await db.commit()
    cur = await db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    return row["count"] if row else 0


async def remove_warn(chat_id: int, user_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    if not row or row["count"] <= 0:
        return 0
    n = row["count"] - 1
    if n == 0:
        await db.execute(
            "DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
    else:
        await db.execute(
            "UPDATE warns SET count=? WHERE chat_id=? AND user_id=?",
            (n, chat_id, user_id),
        )
    await db.commit()
    return n


async def get_warns(chat_id: int, user_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT count FROM warns WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    return row["count"] if row else 0


async def reset_warns(chat_id: int, user_id: int):
    db = await get_db()
    await db.execute(
        "DELETE FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id)
    )
    await db.commit()


async def warns_stats(chat_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) AS users, COALESCE(SUM(count),0) AS total "
        "FROM warns WHERE chat_id=?",
        (chat_id,),
    )
    row = await cur.fetchone()
    return row["users"], row["total"]


async def warns_top(chat_id: int, limit: int = 10):
    db = await get_db()
    cur = await db.execute(
        "SELECT user_id, count FROM warns WHERE chat_id=? "
        "ORDER BY count DESC LIMIT ?",
        (chat_id, limit),
    )
    return await cur.fetchall()


# ---------------- BANS ----------------
async def set_ban(chat_id: int, user_id: int, until_ts: int):
    db = await get_db()
    await db.execute(
        "INSERT INTO bans (chat_id,user_id,until_ts) VALUES (?,?,?) "
        "ON CONFLICT(chat_id,user_id) DO UPDATE SET until_ts=excluded.until_ts",
        (chat_id, user_id, until_ts),
    )
    await db.commit()


async def remove_ban(chat_id: int, user_id: int):
    db = await get_db()
    await db.execute(
        "DELETE FROM bans WHERE chat_id=? AND user_id=?", (chat_id, user_id)
    )
    await db.commit()


async def count_bans(chat_id: int) -> int:
    db = await get_db()
    cur = await db.execute(
        "SELECT COUNT(*) AS c FROM bans WHERE chat_id=?", (chat_id,)
    )
    row = await cur.fetchone()
    return row["c"]


# ---------------- SETTINGS ----------------
ALLOWED_SETTINGS = (
    "antimat", "antiflood", "antilink", "anticaps",
    "welcome", "goodbye", "rules", "welcome_text", "goodbye_text", "slowmode",
)


async def get_settings(chat_id: int) -> dict:
    db = await get_db()
    cur = await db.execute(
        "SELECT * FROM chat_settings WHERE chat_id=?", (chat_id,)
    )
    row = await cur.fetchone()
    if not row:
        await db.execute(
            "INSERT INTO chat_settings (chat_id) VALUES (?)", (chat_id,)
        )
        await db.commit()
        cur = await db.execute(
            "SELECT * FROM chat_settings WHERE chat_id=?", (chat_id,)
        )
        row = await cur.fetchone()
    return dict(row)


async def update_setting(chat_id: int, key: str, value):
    if key not in ALLOWED_SETTINGS:
        raise ValueError(f"Unknown setting: {key}")
    db = await get_db()
    await db.execute(
        f"INSERT INTO chat_settings (chat_id,{key}) VALUES (?,?) "
        f"ON CONFLICT(chat_id) DO UPDATE SET {key}=excluded.{key}",
        (chat_id, value),
    )
    await db.commit()


async def reset_settings(chat_id: int):
    db = await get_db()
    await db.execute("DELETE FROM chat_settings WHERE chat_id=?", (chat_id,))
    await db.commit()


# ---------------- BAD WORDS ----------------
async def add_bad_word(chat_id: int, word: str):
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO custom_bad_words (chat_id,word) VALUES (?,?)",
        (chat_id, word.lower()),
    )
    await db.commit()


async def remove_bad_word(chat_id: int, word: str):
    db = await get_db()
    await db.execute(
        "DELETE FROM custom_bad_words WHERE chat_id=? AND word=?",
        (chat_id, word.lower()),
    )
    await db.commit()


async def get_bad_words(chat_id: int) -> list[str]:
    db = await get_db()
    cur = await db.execute(
        "SELECT word FROM custom_bad_words WHERE chat_id=?", (chat_id,)
    )
    rows = await cur.fetchall()
    return [r["word"] for r in rows]


# ---------------- WHITELIST ----------------
async def add_whitelist(chat_id: int, user_id: int):
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO white_list (chat_id,user_id) VALUES (?,?)",
        (chat_id, user_id),
    )
    await db.commit()


async def remove_whitelist(chat_id: int, user_id: int):
    db = await get_db()
    await db.execute(
        "DELETE FROM white_list WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    await db.commit()


async def is_whitelisted(chat_id: int, user_id: int) -> bool:
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM white_list WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    return (await cur.fetchone()) is not None


# ---------------- KNOWN USERS ----------------
async def remember_user(chat_id: int, username: str, user_id: int):
    if not username:
        return
    db = await get_db()
    await db.execute(
        "INSERT INTO known_users (chat_id,username,user_id) VALUES (?,?,?) "
        "ON CONFLICT(chat_id,username) DO UPDATE SET user_id=excluded.user_id",
        (chat_id, username.lower(), user_id),
    )
    await db.commit()


async def find_user_by_username(chat_id: int, username: str):
    db = await get_db()
    cur = await db.execute(
        "SELECT user_id FROM known_users WHERE chat_id=? AND username=?",
        (chat_id, username.lower()),
    )
    row = await cur.fetchone()
    return row["user_id"] if row else None


# ---------------- SUBSCRIPTION ----------------
async def save_sub_check(user_id: int):
    db = await get_db()
    await db.execute(
        "INSERT INTO subscriptions (user_id,checked_ts) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET checked_ts=excluded.checked_ts",
        (user_id, int(time.time())),
    )
    await db.commit()


async def get_sub_check(user_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT checked_ts FROM subscriptions WHERE user_id=?", (user_id,)
    )
    row = await cur.fetchone()
    return row["checked_ts"] if row else None


# ---------------- SAVED PERMS ----------------
async def save_perms(chat_id: int, user_id: int, perms: str):
    db = await get_db()
    await db.execute(
        "INSERT INTO saved_perms (chat_id,user_id,perms) VALUES (?,?,?) "
        "ON CONFLICT(chat_id,user_id) DO UPDATE SET perms=excluded.perms",
        (chat_id, user_id, perms),
    )
    await db.commit()


async def pop_perms(chat_id: int, user_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT perms FROM saved_perms WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    if not row:
        return None
    await db.execute(
        "DELETE FROM saved_perms WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    await db.commit()
    return row["perms"]


# ---------------- NOTES ----------------
async def set_note(chat_id: int, user_id: int, note: str):
    db = await get_db()
    await db.execute(
        "INSERT INTO notes (chat_id,user_id,note,created_ts) VALUES (?,?,?,?) "
        "ON CONFLICT(chat_id,user_id) DO UPDATE SET "
        "note=excluded.note, created_ts=excluded.created_ts",
        (chat_id, user_id, note, int(time.time())),
    )
    await db.commit()


async def get_note(chat_id: int, user_id: int):
    db = await get_db()
    cur = await db.execute(
        "SELECT note FROM notes WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
    )
    row = await cur.fetchone()
    return row["note"] if row else None


# ---------------- ACTIVITY ----------------
async def track_activity(chat_id: int, user_id: int):
    today = date.today().isoformat()
    db = await get_db()
    await db.execute(
        "INSERT INTO activity (chat_id,user_id,d,count) VALUES (?,?,?,1) "
        "ON CONFLICT(chat_id,user_id,d) DO UPDATE SET count=count+1",
        (chat_id, user_id, today),
    )
    await db.commit()


async def top_active(chat_id: int, limit: int = 10):
    db = await get_db()
    cur = await db.execute(
        "SELECT user_id, SUM(count) AS total FROM activity "
        "WHERE chat_id=? GROUP BY user_id ORDER BY total DESC LIMIT ?",
        (chat_id, limit),
    )
    return await cur.fetchall()


# ---------------- REPORTS ----------------
async def add_report(chat_id, from_id, about_id, message_id, text):
    db = await get_db()
    await db.execute(
        "INSERT INTO reports (chat_id,from_id,about_id,message_id,text,ts) "
        "VALUES (?,?,?,?,?,?)",
        (chat_id, from_id, about_id, message_id, text, int(time.time())),
    )
    await db.commit()


# =============================================================
#                       УТИЛИТЫ
# =============================================================
BAD_ROOTS = [
    "бля", "бляд", "хуй", "хуе", "хуя", "хую", "пизд", "еба", "ебал",
    "ебан", "ебет", "ёб", "сука", "суки", "сучар", "муда", "муди",
    "нах", "пидор", "пидар", "долбо", "гандон", "шлюх", "мраз",
    "fuck", "shit", "bitch", "cunt", "dick", "pussy", "asshole",
]

LEET_MAP = {
    "a": "а", "b": "б", "e": "е", "k": "к", "m": "м", "h": "н",
    "o": "о", "p": "р", "c": "с", "t": "т", "x": "х", "y": "у",
    "0": "о", "1": "и", "3": "з", "4": "ч", "5": "с", "6": "б",
    "8": "в", "@": "а", "$": "с", "!": "и",
}


def normalize(text: str) -> str:
    t = text.lower()
    for k, v in LEET_MAP.items():
        t = t.replace(k, v)
    t = re.sub(r"[^а-яёa-z]", "", t)
    t = re.sub(r"(.)\1{2,}", r"\1\1", t)
    return t


def contains_mat(text: str, custom=None):
    if not text:
        return None
    n = normalize(text)
    for root in BAD_ROOTS:
        if root in n:
            return root
    if custom:
        for w in custom:
            if w.lower() in n:
                return w
    return None


LINK_RE = re.compile(r"(?:https?://|t\.me/|telegram\.me/|www\.)\S+", re.I)


def contains_link(text: str) -> bool:
    return bool(LINK_RE.search(text or ""))


def is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < CAPS_MIN_LEN:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return (upper / len(letters)) * 100 >= CAPS_MIN_PERCENT


def extract_text(message) -> str:
    parts = []
    if message.text:
        parts.append(message.text)
    if message.caption:
        parts.append(message.caption)
    return " ".join(parts)


def parse_duration(text: str):
    m = re.search(r"(\d+)\s*([smhdw])", text.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {
        "s": timedelta(seconds=n), "m": timedelta(minutes=n),
        "h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n),
    }[unit]


def fmt_duration(td: timedelta) -> str:
    s = int(td.total_seconds())
    if s >= 86400: return f"{s // 86400} д."
    if s >= 3600:  return f"{s // 3600} ч."
    if s >= 60:    return f"{s // 60} мин."
    return f"{s} сек."


DEFAULT_RULES = (
    "📜 <b>Правила чата</b>\n\n"
    "1. Уважай участников.\n2. Без мата.\n3. Без спама и рекламы.\n"
    "4. Не оффтопь.\n5. Без NSFW.\n6. Без конфликтов.\n7. Слушай админов.\n\n"
    "⚠️ Наказания: warn → mute → kick → ban."
)
DEFAULT_WELCOME = "👋 Добро пожаловать, {mention}!\nПрочитай /rules."
DEFAULT_GOODBYE = "👋 {name} покинул(а) чат."

NO_TARGET = (
    "❌ Не нашёл юзера.\n\n"
    "• Ответь <b>реплаем</b> на сообщение\n"
    "• Или укажи числовой ID (/id реплаем)\n"
    "• @username работает, если юзер уже писал в чате"
)
NOT_ADMIN = "⛔ Только для администраторов."
NOT_ENOUGH_RIGHTS = (
    "⚠️ У меня нет прав.\nВыдай: <b>Ограничивать участников</b>, "
    "<b>Блокировать участников</b>, <b>Удалять сообщения</b>."
)


def user_error():
    return "❌ Не удалось выполнить."


# ---------------- ADMIN CACHE ----------------
_admin_cache: dict[tuple[int, int], tuple[float, bool]] = {}
ADMIN_CACHE_TTL = 60


async def is_admin_cached(bot: Bot, chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    now = time.time()
    if key in _admin_cache:
        ts, val = _admin_cache[key]
        if now - ts < ADMIN_CACHE_TTL:
            return val
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        ok = m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        ok = False
    _admin_cache[key] = (now, ok)
    return ok


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        if not message.from_user:
            return False
        return await is_admin_cached(
            message.bot, message.chat.id, message.from_user.id
        )


async def resolve_target(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    args = message.text.split()[1:] if message.text else []
    if not args:
        return None
    arg = args[0]
    if arg.startswith("@"):
        return await find_user_by_username(message.chat.id, arg[1:])
    if arg.lstrip("-").isdigit():
        return int(arg)
    return None


async def ensure_rights(bot: Bot, chat_id: int) -> bool:
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
        return bool(getattr(me, "can_restrict_members", False))
    except Exception:
        return False


async def _save_current_perms(bot: Bot, chat_id: int, user_id: int):
    try:
        cur = await bot.get_chat_member(chat_id, user_id)
        perms = {
            k: bool(getattr(cur, k, False))
            for k in (
                "can_send_messages", "can_send_media_messages",
                "can_send_other_messages", "can_add_web_page_previews",
                "can_send_polls", "can_invite_users",
            )
        }
        await save_perms(chat_id, user_id, json.dumps(perms))
    except Exception:
        log.exception("save perms failed")


MUTE_PERMS = ChatPermissions(can_send_messages=False)
READONLY_PERMS = ChatPermissions(can_send_messages=False)
UNMUTE_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_send_polls=True,
    can_invite_users=True,
)


# =============================================================
#                       MIDDLEWARE ПОДПИСКИ
# =============================================================
def sub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Проверить", callback_data="check_sub")],
    ])


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        chat = getattr(event, "chat", None)
        if chat is None and isinstance(event, CallbackQuery):
            chat = event.message.chat if event.message else None
        if chat is None or chat.type != "private":
            return await handler(event, data)

        user = event.from_user
        if not user:
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == "check_sub":
            return await handler(event, data)
        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            return await handler(event, data)

        checked = await get_sub_check(user.id)
        now = int(time.time())
        if checked and (now - checked) < SUB_RECHECK_INTERVAL:
            return await handler(event, data)

        try:
            m = await event.bot.get_chat_member(CHANNEL_USERNAME, user.id)
            ok = m.status in (
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR,
            )
        except Exception:
            ok = False

        if ok:
            await save_sub_check(user.id)
            return await handler(event, data)

        text = f"🔒 Подпишись на {CHANNEL_USERNAME}, чтобы пользоваться ботом."
        if isinstance(event, Message):
            await event.answer(text, reply_markup=sub_kb())
        else:
            await event.answer("Подпишись сначала!", show_alert=True)
        return None


# =============================================================
#                       БОТ И РОУТЕР
# =============================================================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
dp.message.middleware(SubscriptionMiddleware())
dp.callback_query.middleware(SubscriptionMiddleware())


async def _autodelete(chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _warn_msg(message: Message, text: str):
    try:
        msg = await message.answer(text)
        asyncio.create_task(
            _autodelete(message.chat.id, msg.message_id, AUTODELETE_SECONDS)
        )
    except Exception:
        log.exception("warn send failed")


# =============================================================
#                       БАЗОВЫЕ КОМАНДЫ
# =============================================================
@dp.message(CommandStart())
async def start_cmd(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "Привет! Я <b>Jopa Менеджер</b> 💫\n\n"
            "Добавь меня в группу и выдай права.\n"
            "📖 Команды: /help",
        )
    else:
        await message.answer("Jopa Менеджер на связи 💫 /help")


@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery):
    try:
        m = await bot.get_chat_member(CHANNEL_USERNAME, call.from_user.id)
        ok = m.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except Exception:
        ok = False
    if ok:
        await save_sub_check(call.from_user.id)
        await call.message.edit_text("✅ Подписка подтверждена! Пиши /help.")
    else:
        await call.answer("❌ Ты ещё не подписался!", show_alert=True)


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "🛡 <b>Jopa Менеджер — команды</b>\n\n"
        "<b>🔨 Наказания:</b>\n"
        "/ban /unban /kick /softban /tempban\n"
        "/mute /unmute /ro\n"
        "/warn /unwarn /warns /warns_top\n\n"
        "<b>👑 Роли:</b>\n"
        "/promote /demote\n\n"
        "<b>📝 Заметки:</b>\n"
        "/note /delnote /history\n\n"
        "<b>⚙️ Управление:</b>\n"
        "/setrules /rules\n"
        "/antimat on|off\n"
        "/antimat_add /antimat_del /antimat_list\n"
        "/antilink on|off | /anticaps on|off\n"
        "/antiflood on|off | /slowmode [сек]\n"
        "/lock /unlock\n"
        "/welcome on|off | /setwelcome\n"
        "/goodbye on|off | /setgoodbye\n\n"
        "<b>🔒 Whitelist:</b>\n"
        "/whitelist /unwhitelist\n\n"
        "<b>ℹ️ Инфо:</b>\n"
        "/user /chat /admins /stats /top /id\n\n"
        "<b>🎲 Развлечения:</b>\n"
        "/dice /coin /roll /8ball /slap /hug\n\n"
        "<b>📢 Прочее:</b>\n"
        "/report /settings /reset\n\n"
        "💡 Срок: <code>s/m/h/d/w</code>\n"
        "💡 Реплай — цель оттуда.",
    )


@dp.message(Command("rules"))
async def rules_cmd(message: Message):
    if message.chat.type == "private":
        return await message.answer(DEFAULT_RULES)
    s = await get_settings(message.chat.id)
    await message.reply(s["rules"] or DEFAULT_RULES)


@dp.message(Command("id"))
async def id_cmd(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        text = f"👤 {u.full_name}\n🆔 <code>{u.id}</code>"
        if u.username:
            text += f"\n🔗 @{u.username}"
        await message.reply(text)
    else:
        await message.reply(f"Твой ID: <code>{message.from_user.id}</code>")


# =============================================================
#                       МОДЕРАЦИЯ
# =============================================================
@dp.message(Command("ban"), IsAdmin())
async def ban_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await ensure_rights(bot, message.chat.id):
        return await message.reply(NOT_ENOUGH_RIGHTS)
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    dur = parse_duration(message.text) or timedelta(days=1)
    until = datetime.now() + dur
    try:
        await bot.ban_chat_member(message.chat.id, target, until_date=until)
        await set_ban(message.chat.id, target, int(until.timestamp()))
        await message.reply(
            f"🔨 Забанен <code>{target}</code> на {fmt_duration(dur)}."
        )
    except Exception:
        log.exception("ban failed")
        await message.reply(user_error())


@dp.message(Command("tempban"), IsAdmin())
async def tempban_cmd(message: Message):
    await ban_cmd(message)


@dp.message(Command("softban"), IsAdmin())
async def softban_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await ensure_rights(bot, message.chat.id):
        return await message.reply(NOT_ENOUGH_RIGHTS)
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    try:
        await bot.ban_chat_member(message.chat.id, target)
        await bot.unban_chat_member(message.chat.id, target)
        await message.reply(f"🧹 Softban: сообщения <code>{target}</code> удалены.")
    except Exception:
        log.exception("softban failed")
        await message.reply(user_error())


@dp.message(Command("unban"), IsAdmin())
async def unban_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    try:
        await bot.unban_chat_member(message.chat.id, target, only_if_banned=True)
        await remove_ban(message.chat.id, target)
        await message.reply(f"✅ Разбанен <code>{target}</code>.")
    except Exception:
        log.exception("unban failed")
        await message.reply(user_error())


@dp.message(Command("kick"), IsAdmin())
async def kick_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await ensure_rights(bot, message.chat.id):
        return await message.reply(NOT_ENOUGH_RIGHTS)
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    try:
        await bot.ban_chat_member(message.chat.id, target)
        await bot.unban_chat_member(message.chat.id, target)
        await message.reply(f"👢 Кикнут <code>{target}</code>.")
    except Exception:
        log.exception("kick failed")
        await message.reply(user_error())


@dp.message(Command("mute"), IsAdmin())
async def mute_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await ensure_rights(bot, message.chat.id):
        return await message.reply(NOT_ENOUGH_RIGHTS)
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    dur = parse_duration(message.text) or timedelta(hours=1)
    until = datetime.now() + dur
    await _save_current_perms(bot, message.chat.id, target)
    try:
        await bot.restrict_chat_member(
            message.chat.id, target,
            permissions=MUTE_PERMS, until_date=until,
        )
        await message.reply(
            f"🔇 Замучен <code>{target}</code> на {fmt_duration(dur)}."
        )
    except Exception:
        log.exception("mute failed")
        await message.reply(user_error())


@dp.message(Command("ro"), IsAdmin())
async def ro_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await ensure_rights(bot, message.chat.id):
        return await message.reply(NOT_ENOUGH_RIGHTS)
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    dur = parse_duration(message.text) or timedelta(hours=1)
    until = datetime.now() + dur
    await _save_current_perms(bot, message.chat.id, target)
    try:
        await bot.restrict_chat_member(
            message.chat.id, target,
            permissions=READONLY_PERMS, until_date=until,
        )
        await message.reply(
            f"📖 <code>{target}</code> только-чтение на {fmt_duration(dur)}."
        )
    except Exception:
        log.exception("ro failed")
        await message.reply(user_error())


@dp.message(Command("unmute"), IsAdmin())
async def unmute_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await ensure_rights(bot, message.chat.id):
        return await message.reply(NOT_ENOUGH_RIGHTS)
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    saved = await pop_perms(message.chat.id, target)
    perms = ChatPermissions(**json.loads(saved)) if saved else UNMUTE_PERMS
    try:
        await bot.restrict_chat_member(
            message.chat.id, target, permissions=perms
        )
        await message.reply(f"🔊 Размучен <code>{target}</code>.")
    except Exception:
        log.exception("unmute failed")
        await message.reply(user_error())


@dp.message(Command("warn"), IsAdmin())
async def warn_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    count = await add_warn(message.chat.id, target)
    if count >= WARN_LIMIT:
        until = datetime.now() + timedelta(hours=WARN_MUTE_HOURS)
        try:
            await bot.restrict_chat_member(
                message.chat.id, target,
                permissions=MUTE_PERMS, until_date=until,
            )
            await reset_warns(message.chat.id, target)
            await message.reply(
                f"⚠️ <code>{target}</code> — {WARN_LIMIT} варна → мут на "
                f"{WARN_MUTE_HOURS} ч."
            )
        except Exception:
            log.exception("warn-mute failed")
            await message.reply(user_error())
    else:
        await message.reply(f"⚠️ Варн <code>{target}</code>: {count}/{WARN_LIMIT}")


@dp.message(Command("unwarn"), IsAdmin())
async def unwarn_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    left = await remove_warn(message.chat.id, target)
    await message.reply(
        f"✅ Снят варн с <code>{target}</code>. Осталось: {left}"
    )


@dp.message(Command("warns"), IsAdmin())
async def warns_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message) or message.from_user.id
    count = await get_warns(message.chat.id, target)
    await message.reply(f"Варны <code>{target}</code>: {count}/{WARN_LIMIT}")


@dp.message(Command("warns_top"), IsAdmin())
async def warns_top_cmd(message: Message):
    if message.chat.type == "private":
        return
    rows = await warns_top(message.chat.id, 10)
    if not rows:
        return await message.reply("Варнов ни у кого нет ✨")
    lines = ["🏆 <b>Топ по варнам:</b>\n"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. <code>{r['user_id']}</code> — {r['count']}")
    await message.reply("\n".join(lines))


# =============================================================
#                       РОЛИ
# =============================================================
@dp.message(Command("promote"), IsAdmin())
async def promote_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    try:
        await bot.promote_chat_member(
            message.chat.id, target,
            can_manage_chat=True, can_delete_messages=True,
            can_restrict_members=True, can_invite_users=True,
            can_pin_messages=True,
        )
        await message.reply(f"👑 <code>{target}</code> повышен.")
    except Exception:
        log.exception("promote failed")
        await message.reply(user_error())


@dp.message(Command("demote"), IsAdmin())
async def demote_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    try:
        await bot.promote_chat_member(
            message.chat.id, target,
            can_manage_chat=False, can_delete_messages=False,
            can_restrict_members=False, can_invite_users=False,
            can_pin_messages=False, can_change_info=False,
        )
        await message.reply(f"⬇️ <code>{target}</code> понижен.")
    except Exception:
        log.exception("demote failed")
        await message.reply(user_error())


# =============================================================
#                       ЗАМЕТКИ
# =============================================================
@dp.message(Command("note"), IsAdmin())
async def note_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    parts = message.text.split(maxsplit=2)
    text = parts[2] if len(parts) >= 3 else ""
    if not text:
        return await message.reply("Напиши текст заметки после команды.")
    await set_note(message.chat.id, target, text)
    await message.reply(f"📝 Заметка на <code>{target}</code> сохранена.")


@dp.message(Command("delnote"), IsAdmin())
async def delnote_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    await set_note(message.chat.id, target, "")
    await message.reply("🗑 Заметка удалена.")


@dp.message(Command("history"), IsAdmin())
async def history_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    note = await get_note(message.chat.id, target)
    warns = await get_warns(message.chat.id, target)
    await message.reply(
        f"📋 <b>История</b> <code>{target}</code>\n"
        f"• Варнов: {warns}/{WARN_LIMIT}\n"
        f"• Заметка: {note or '—'}"
    )


# =============================================================
#                       НАСТРОЙКИ
# =============================================================
@dp.message(Command("setrules"), IsAdmin())
async def setrules_cmd(message: Message):
    if message.chat.type == "private":
        return
    text = message.text.partition(" ")[2].strip()
    if not text:
        return await message.reply("Напиши текст после команды.")
    await update_setting(message.chat.id, "rules", text)
    await message.reply("✅ Правила обновлены.")


@dp.message(Command("antimat"), IsAdmin())
async def antimat_cmd(message: Message):
    if message.chat.type == "private":
        return
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        await update_setting(message.chat.id, "antimat", 1)
        await message.reply("✅ Антимат ВКЛ.")
    elif arg == "off":
        await update_setting(message.chat.id, "antimat", 0)
        await message.reply("❌ Антимат ВЫКЛ.")
    else:
        s = await get_settings(message.chat.id)
        await message.reply(f"Антимат: {'вкл' if s['antimat'] else 'выкл'}")


@dp.message(Command("antimat_add"), IsAdmin())
async def antimat_add_cmd(message: Message):
    if message.chat.type == "private":
        return
    word = message.text.partition(" ")[2].strip().lower()
    if not word:
        return await message.reply("Напиши слово.")
    await add_bad_word(message.chat.id, word)
    await message.reply(f"✅ Добавлено: <code>{word}</code>")


@dp.message(Command("antimat_del"), IsAdmin())
async def antimat_del_cmd(message: Message):
    if message.chat.type == "private":
        return
    word = message.text.partition(" ")[2].strip().lower()
    if not word:
        return await message.reply("Напиши слово.")
    await remove_bad_word(message.chat.id, word)
    await message.reply(f"🗑 Удалено: <code>{word}</code>")


@dp.message(Command("antimat_list"), IsAdmin())
async def antimat_list_cmd(message: Message):
    if message.chat.type == "private":
        return
    words = await get_bad_words(message.chat.id)
    if not words:
        return await message.reply("Своих стоп-слов нет.")
    await message.reply(
        "🚫 Свои стоп-слова:\n" + ", ".join(f"<code>{w}</code>" for w in words)
    )


@dp.message(Command("antilink"), IsAdmin())
async def antilink_cmd(message: Message):
    if message.chat.type == "private":
        return
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        await update_setting(message.chat.id, "antilink", 1)
        await message.reply("✅ Антилинк ВКЛ.")
    elif arg == "off":
        await update_setting(message.chat.id, "antilink", 0)
        await message.reply("❌ Антилинк ВЫКЛ.")
    else:
        s = await get_settings(message.chat.id)
        await message.reply(f"Антилинк: {'вкл' if s['antilink'] else 'выкл'}")


@dp.message(Command("anticaps"), IsAdmin())
async def anticaps_cmd(message: Message):
    if message.chat.type == "private":
        return
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        await update_setting(message.chat.id, "anticaps", 1)
        await message.reply("✅ Антикапс ВКЛ.")
    elif arg == "off":
        await update_setting(message.chat.id, "anticaps", 0)
        await message.reply("❌ Антикапс ВЫКЛ.")
    else:
        s = await get_settings(message.chat.id)
        await message.reply(f"Антикапс: {'вкл' if s['anticaps'] else 'выкл'}")


@dp.message(Command("antiflood"), IsAdmin())
async def antiflood_cmd(message: Message):
    if message.chat.type == "private":
        return
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        await update_setting(message.chat.id, "antiflood", 1)
        await message.reply("✅ Антифлуд ВКЛ.")
    elif arg == "off":
        await update_setting(message.chat.id, "antiflood", 0)
        await message.reply("❌ Антифлуд ВЫКЛ.")
    else:
        s = await get_settings(message.chat.id)
        await message.reply(f"Антифлуд: {'вкл' if s['antiflood'] else 'выкл'}")


@dp.message(Command("slowmode"), IsAdmin())
async def slowmode_cmd(message: Message):
    if message.chat.type == "private":
        return
    m = re.search(r"(\d+)", message.text)
    n = int(m.group(1)) if m else 0
    if n < 0 or n > 3600:
        return await message.reply("0-3600 сек. 0 — выкл.")
    try:
        await bot.set_chat_slow_mode(message.chat.id, slow_mode_delay=n)
        await message.reply(f"🐌 Slowmode: {n} сек.")
    except Exception:
        log.exception("slowmode failed")
        await message.reply(user_error())


@dp.message(Command("lock"), IsAdmin())
async def lock_cmd(message: Message):
    if message.chat.type == "private":
        return
    try:
        await bot.set_chat_permissions(
            message.chat.id, ChatPermissions(can_send_messages=False)
        )
        await message.reply("🔒 Чат закрыт.")
    except Exception:
        log.exception("lock failed")
        await message.reply(user_error())


@dp.message(Command("unlock"), IsAdmin())
async def unlock_cmd(message: Message):
    if message.chat.type == "private":
        return
    try:
        await bot.set_chat_permissions(
            message.chat.id,
            ChatPermissions(
                can_send_messages=True, can_send_media_messages=True,
                can_send_other_messages=True, can_add_web_page_previews=True,
                can_send_polls=True, can_invite_users=True,
            ),
        )
        await message.reply("🔓 Чат открыт.")
    except Exception:
        log.exception("unlock failed")
        await message.reply(user_error())


@dp.message(Command("welcome"), IsAdmin())
async def welcome_cmd(message: Message):
    if message.chat.type == "private":
        return
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        await update_setting(message.chat.id, "welcome", 1)
        await message.reply("✅ Приветствие ВКЛ.")
    elif arg == "off":
        await update_setting(message.chat.id, "welcome", 0)
        await message.reply("❌ Приветствие ВЫКЛ.")
    else:
        s = await get_settings(message.chat.id)
        await message.reply(f"Приветствие: {'вкл' if s['welcome'] else 'выкл'}")


@dp.message(Command("goodbye"), IsAdmin())
async def goodbye_cmd(message: Message):
    if message.chat.type == "private":
        return
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        await update_setting(message.chat.id, "goodbye", 1)
        await message.reply("✅ Прощание ВКЛ.")
    elif arg == "off":
        await update_setting(message.chat.id, "goodbye", 0)
        await message.reply("❌ Прощание ВЫКЛ.")
    else:
        s = await get_settings(message.chat.id)
        await message.reply(f"Прощание: {'вкл' if s['goodbye'] else 'выкл'}")


@dp.message(Command("setwelcome"), IsAdmin())
async def setwelcome_cmd(message: Message):
    if message.chat.type == "private":
        return
    text = message.text.partition(" ")[2].strip()
    if not text:
        return await message.reply("Доступно: {mention}, {name}, {chat}")
    await update_setting(message.chat.id, "welcome_text", text)
    await message.reply("✅ Приветствие обновлено.")


@dp.message(Command("setgoodbye"), IsAdmin())
async def setgoodbye_cmd(message: Message):
    if message.chat.type == "private":
        return
    text = message.text.partition(" ")[2].strip()
    if not text:
        return await message.reply("Доступно: {name}, {chat}")
    await update_setting(message.chat.id, "goodbye_text", text)
    await message.reply("✅ Прощание обновлено.")


@dp.message(Command("whitelist"), IsAdmin())
async def whitelist_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    await add_whitelist(message.chat.id, target)
    await message.reply(f"✅ <code>{target}</code> в белом списке.")


@dp.message(Command("unwhitelist"), IsAdmin())
async def unwhitelist_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message)
    if not target:
        return await message.reply(NO_TARGET)
    await remove_whitelist(message.chat.id, target)
    await message.reply(f"🗑 <code>{target}</code> убран.")


@dp.message(Command("settings"), IsAdmin())
async def settings_cmd(message: Message):
    if message.chat.type == "private":
        return
    s = await get_settings(message.chat.id)
    f = lambda v: "✅" if v else "❌"
    await message.reply(
        f"⚙️ <b>Настройки</b>\n\n"
        f"Антимат: {f(s['antimat'])}\n"
        f"Антифлуд: {f(s['antiflood'])}\n"
        f"Антилинк: {f(s['antilink'])}\n"
        f"Антикапс: {f(s['anticaps'])}\n"
        f"Приветствие: {f(s['welcome'])}\n"
        f"Прощание: {f(s['goodbye'])}\n"
        f"Slowmode: {s['slowmode']} сек\n"
        f"Правила: {'✅' if s['rules'] else '❌'}"
    )


@dp.message(Command("reset"), IsAdmin())
async def reset_cmd(message: Message):
    if message.chat.type == "private":
        return
    await reset_settings(message.chat.id)
    await message.reply("♻️ Настройки сброшены.")


@dp.message(Command("stats"), IsAdmin())
async def stats_cmd(message: Message):
    if message.chat.type == "private":
        return
    cid = message.chat.id
    s = await get_settings(cid)
    users, total = await warns_stats(cid)
    bans = await count_bans(cid)
    await message.reply(
        f"📊 <b>Статистика</b>\n"
        f"• Юзеров с варнами: {users}\n"
        f"• Всего варнов: {total}\n"
        f"• Активных банов: {bans}\n"
        f"• Антимат: {'вкл' if s['antimat'] else 'выкл'}\n"
        f"• Антифлуд: {'вкл' if s['antiflood'] else 'выкл'}"
    )


# =============================================================
#                       ИНФО
# =============================================================
@dp.message(Command("user"))
async def user_cmd(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
    else:
        u = message.from_user
    warns = 0
    note = None
    if message.chat.type != "private":
        warns = await get_warns(message.chat.id, u.id)
        note = await get_note(message.chat.id, u.id)
    await message.reply(
        f"👤 <b>{u.full_name}</b>\n"
        f"🆔 <code>{u.id}</code>\n"
        f"🔗 @{u.username or '—'}\n"
        f"⚠️ Варнов: {warns}\n"
        f"📝 Заметка: {note or '—'}"
    )


@dp.message(Command("chat"))
async def chat_cmd(message: Message):
    if message.chat.type == "private":
        return
    c = message.chat
    try:
        count = await bot.get_chat_member_count(c.id)
    except Exception:
        count = "—"
    await message.reply(
        f"💬 <b>{c.title}</b>\n"
        f"🆔 <code>{c.id}</code>\n"
        f"👥 {count}\n"
        f"🔗 @{c.username or '—'}"
    )


@dp.message(Command("admins"))
async def admins_cmd(message: Message):
    if message.chat.type == "private":
        return
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
    except Exception:
        return await message.reply("Не удалось получить список.")
    lines = ["👑 <b>Админы:</b>\n"]
    for a in admins:
        if a.user.is_bot:
            continue
        tag = "👑" if a.status == ChatMemberStatus.CREATOR else "🛡"
        lines.append(f"{tag} {a.user.full_name} — @{a.user.username or a.user.id}")
    await message.reply("\n".join(lines))


@dp.message(Command("top"))
async def top_cmd(message: Message):
    if message.chat.type == "private":
        return
    rows = await top_active(message.chat.id, 10)
    if not rows:
        return await message.reply("Активности нет.")
    lines = ["🏆 <b>Топ активных:</b>\n"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. <code>{r['user_id']}</code> — {r['total']} сообщ.")
    await message.reply("\n".join(lines))


# =============================================================
#                       ЖАЛОБЫ
# =============================================================
@dp.message(Command("report"))
async def report_cmd(message: Message):
    if message.chat.type == "private":
        return await message.reply("Только в группе.")
    if not message.reply_to_message:
        return await message.reply("Ответь реплаем на сообщение нарушителя.")
    reported = message.reply_to_message.from_user
    if not reported:
        return await message.reply("Не могу определить автора.")
    reason = message.text.partition(" ")[2].strip() or "без причины"
    await add_report(
        message.chat.id, message.from_user.id, reported.id,
        message.reply_to_message.message_id, reason,
    )
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
    except Exception:
        admins = []
    mentions = [
        a.user.mention for a in admins
        if not a.user.is_bot and a.status in (
            ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR
        )
    ]
    await message.reply(
        f"🚨 Жалоба на {reported.mention}!\n"
        f"Причина: {reason}\n" +
        ("📣 " + " ".join(mentions[:5]) if mentions else "")
    )


# =============================================================
#                       РАЗВЛЕЧЕНИЯ
# =============================================================
EIGHT_BALL = [
    "Да ✅", "Нет ❌", "Возможно 🤔", "Определённо да!",
    "Спроси позже...", "Не могу сказать", "Скорее да", "Скорее нет",
    "100% да", "Даже не думай",
]
SLAPS = [
    "{a} отвешивает оплеуху {b} 👋",
    "{a} отправляет {b} в нокаут 🥊",
    "{a} кидает тапок в {b} 🥿",
]
HUGS = [
    "{a} крепко обнимает {b} 🤗",
    "{a} дарит тёплые объятия {b} 🫂",
]


@dp.message(Command("dice"))
async def dice_cmd(message: Message):
    await message.answer_dice(emoji="🎲")


@dp.message(Command("coin"))
async def coin_cmd(message: Message):
    await message.reply(random.choice(["🪙 Орёл", "🪙 Решка"]))


@dp.message(Command("roll"))
async def roll_cmd(message: Message):
    m = re.search(r"(\d+)", message.text)
    n = int(m.group(1)) if m else 100
    if n < 2 or n > 1_000_000:
        return await message.reply("Число от 2 до 1000000.")
    await message.reply(f"🎯 {random.randint(1, n)}")


@dp.message(Command("8ball"))
async def ball_cmd(message: Message):
    q = message.text.partition(" ")[2].strip()
    if not q:
        return await message.reply("Задай вопрос.")
    await message.reply(f"🎱 {random.choice(EIGHT_BALL)}")


@dp.message(Command("slap"))
async def slap_cmd(message: Message):
    if not message.reply_to_message:
        return await message.reply("Ответь реплаем.")
    a = message.from_user.first_name
    b = message.reply_to_message.from_user.first_name
    await message.reply(random.choice(SLAPS).format(a=a, b=b))


@dp.message(Command("hug"))
async def hug_cmd(message: Message):
    if not message.reply_to_message:
        return await message.reply("Ответь реплаем.")
    a = message.from_user.first_name
    b = message.reply_to_message.from_user.first_name
    await message.reply(random.choice(HUGS).format(a=a, b=b))


# =============================================================
#                       СОБЫТИЯ: ВХОД/ВЫХОД
# =============================================================
@dp.message(F.new_chat_members)
async def welcome_evt(message: Message):
    settings = await get_settings(message.chat.id)
    if not settings["welcome"]:
        return
    template = settings["welcome_text"] or DEFAULT_WELCOME
    for user in message.new_chat_members:
        if user.id == bot.id:
            continue
        try:
            text = template.format(
                mention=user.mention,
                name=user.full_name,
                chat=message.chat.title or "чат",
            )
            await message.answer(text)
        except Exception:
            log.exception("welcome failed")


@dp.message(F.left_chat_member)
async def goodbye_evt(message: Message):
    settings = await get_settings(message.chat.id)
    if not settings["goodbye"]:
        return
    u = message.left_chat_member
    if not u or u.id == bot.id:
        return
    template = settings["goodbye_text"] or DEFAULT_GOODBYE
    try:
        await message.answer(template.format(name=u.full_name,
                                              chat=message.chat.title or "чат"))
    except Exception:
        log.exception("goodbye failed")


# =============================================================
#                       АВТОМОДЕРАЦИЯ
# =============================================================
_flood_log: dict[tuple[int, int], list[float]] = {}


@dp.message(F.text | F.caption, ~F.text.startswith("/"))
async def automod(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    if not message.from_user:
        return

    # запоминаем юзера для @username
    if message.from_user.username:
        try:
            await remember_user(
                message.chat.id, message.from_user.username, message.from_user.id
            )
        except Exception:
            pass

    # трекаем активность
    try:
        await track_activity(message.chat.id, message.from_user.id)
    except Exception:
        pass

    if await is_admin_cached(bot, message.chat.id, message.from_user.id):
        return
    if await is_whitelisted(message.chat.id, message.from_user.id):
        return

    settings = await get_settings(message.chat.id)
    text = extract_text(message)

    # антимат
    if settings["antimat"] and text:
        custom = await get_bad_words(message.chat.id)
        root = contains_mat(text, custom)
        if root:
            try:
                await message.delete()
            except Exception:
                pass
            await _warn_msg(message, f"🚫 {message.from_user.mention}, мат!")
            return

    # антилинк
    if settings["antilink"] and text and contains_link(text):
        try:
            await message.delete()
        except Exception:
            pass
        await _warn_msg(message, f"🔗 {message.from_user.mention}, ссылки запрещены!")
        return

    # антикапс
    if settings["anticaps"] and text and is_caps(text):
        try:
            await message.delete()
        except Exception:
            pass
        await _warn_msg(message, f"🔠 {message.from_user.mention}, не капси!")
        return

    # антифлуд
    if settings["antiflood"]:
        key = (message.chat.id, message.from_user.id)
        now = asyncio.get_event_loop().time()
        lst = _flood_log.setdefault(key, [])
        lst.append(now)
        lst[:] = [t for t in lst if now - t < FLOOD_WINDOW]
        if len(lst) >= FLOOD_LIMIT:
            try:
                await message.delete()
            except Exception:
                pass
            until = datetime.now() + timedelta(minutes=FLOOD_MUTE_MINUTES)
            try:
                await bot.restrict_chat_member(
                    message.chat.id, message.from_user.id,
                    permissions=MUTE_PERMS, until_date=until,
                )
                await _warn_msg(
                    message,
                    f"🚫 {message.from_user.mention} мут {FLOOD_MUTE_MINUTES} мин.",
                )
            except Exception:
                log.exception("flood mute failed")
            finally:
                _flood_log[key] = []


# =============================================================
#                       СТАРТ
# =============================================================
async def main():
    await init_db()
    log.info("БД готова")
    log.info("Jopa Менеджер запущен 🚀")
    try:
        await dp.start_polling(bot)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
