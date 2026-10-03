import os
import re
import random
import asyncio
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    ChatPermissions,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatMemberStatus, ChatAction

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Канал для обязательной подписки
CHANNEL_USERNAME = "@jopmenjer"      # @username канала
CHANNEL_URL = "https://t.me/jopmenjer"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ==================== ХРАНИЛИЩЕ ====================
warns: dict[int, dict[int, int]] = {}            # {chat_id: {user_id: count}}
bans: dict[int, dict[int, datetime]] = {}        # {chat_id: {user_id: until}}
rules_text: dict[int, str] = {}
antimat_on: dict[int, bool] = {}
antiflood_on: dict[int, bool] = {}
flood_log: dict[int, dict[int, list[float]]] = {}
known_users: dict[int, dict[str, int]] = {}      # {@username: user_id}
subscribe_passed: set[int] = set()               # кто прошёл подписку

DEFAULT_RULES = (
    "📜 <b>Правила чата</b>\n\n"
    "1. Уважай участников — без оскорблений и травли.\n"
    "2. Запрещён мат и нецензурная лексика.\n"
    "3. Без спама, флуда и рекламы.\n"
    "4. Не оффтопь.\n"
    "5. Запрещены NSFW и шок-контент.\n"
    "6. Не разжигай конфликты на почве религии, политики, нации.\n"
    "7. Слушай администрацию — её слово последнее.\n\n"
    "⚠️ Наказания: предупреждение → мут → кик → бан."
)

BAD_WORDS = [
    r"\bбля\w*", r"\bхуй\w*", r"\bпизд\w*", r"\bеба\w*", r"\bёб\w*",
    r"\bсук\w*", r"\bмуд\w*", r"\bнах\w*", r"\bпидор\w*", r"\bдолбо\w*",
]
WARN_LIMIT = 3
FLOOD_LIMIT = 5
FLOOD_WINDOW = 5  # сек


# ==================== УТИЛИТЫ ====================
async def is_admin(chat_id: int, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False


async def check_sub(user_id: int) -> bool:
    """Проверка подписки на канал."""
    try:
        m = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return m.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except Exception:
        return False


def sub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")],
    ])


async def need_sub(message: Message) -> bool:
    """True — если НЕ подписан (и уже показали сообщение)."""
    uid = message.from_user.id
    if uid in subscribe_passed:
        return False
    if await check_sub(uid):
        subscribe_passed.add(uid)
        return False
    await message.answer(
        f"🔒 Чтобы пользоваться <b>Jopa Менеджер</b>,\n"
        f"подпишись на канал {CHANNEL_USERNAME}.\n\n"
        f"После подписки нажми «✅ Проверить подписку».",
        parse_mode="HTML",
        reply_markup=sub_kb(),
    )
    return True


async def resolve_target(message: Message) -> int | None:
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    args = message.text.split()[1:] if message.text else []
    if not args:
        return None
    arg = args[0]
    if arg.startswith("@"):
        return known_users.get(message.chat.id, {}).get(arg[1:].lower())
    if arg.lstrip("-").isdigit():
        return int(arg)
    return None


def parse_duration(text: str) -> timedelta | None:
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


# ==================== КОМАНДЫ ====================
@dp.message(CommandStart())
async def start_cmd(message: Message):
    if message.chat.type == "private":
        if await need_sub(message):
            return
        await message.answer(
            "Привет! Я <b>Jopa Менеджер</b> 💫\n\n"
            "Добавь меня в группу и выдай права администратора — "
            "я буду следить за порядком.\n\n"
            "📖 Команды: /help",
            parse_mode="HTML",
        )
    else:
        await message.answer("Jopa Менеджер на связи 💫 /help — команды.")


@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(call: CallbackQuery):
    if await check_sub(call.from_user.id):
        subscribe_passed.add(call.from_user.id)
        await call.message.edit_text(
            "✅ Подписка подтверждена!\n\n"
            "Напиши /help, чтобы узнать команды."
        )
    else:
        await call.answer("❌ Ты ещё не подписался!", show_alert=True)


@dp.message(Command("help"))
async def help_cmd(message: Message):
    if message.chat.type == "private" and await need_sub(message):
        return
    await message.answer(
        "🛡 <b>Jopa Менеджер — команды</b>\n\n"
        "<b>Наказания:</b>\n"
        "/ban [юзер/id] [срок] — забанить (<code>/ban 3d</code> реплаем)\n"
        "/unban [id] — разбанить\n"
        "/kick [юзер/id] — кикнуть\n"
        "/mute [юзер/id] [срок] — замутить\n"
        "/unmute [юзер/id] — размутить\n"
        "/warn [юзер/id] — варн (3 = автомут)\n"
        "/unwarn [юзер/id] — снять варн\n"
        "/warns [юзер/id] — посмотреть варны\n\n"
        "<b>Управление:</b>\n"
        "/setrules [текст] — задать правила\n"
        "/rules — показать правила\n"
        "/antimat on|off — фильтр мата\n"
        "/antiflood on|off — фильтр флуда\n"
        "/pin — закрепить (реплай)\n"
        "/purge [N] — удалить N сообщений\n"
        "/stats — статистика\n"
        "/id — узнать ID (реплай)\n\n"
        "<b>Развлечения:</b>\n"
        "/dice — кубик 🎲\n"
        "/roll [N] — случайное число 1..N\n"
        "/coin — монетка 🪙\n\n"
        "💡 Отвечай реплаем — цель берётся оттуда.",
        parse_mode="HTML",
    )


# ---------- Модерация ----------
@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    target = await resolve_target(message)
    if not target:
        return await message.reply(
            "Не нашёл юзера.\n"
            "• Ответь реплаем на сообщение\n"
            "• Или укажи числовой ID (/id реплаем)"
        )
    dur = parse_duration(message.text) or timedelta(days=1)
    until = datetime.now() + dur
    bans.setdefault(message.chat.id, {})[target] = until
    try:
        await bot.ban_chat_member(message.chat.id, target, until_date=until)
        await message.reply(
            f"🔨 Забанен <code>{target}</code> на {fmt_duration(dur)}.\n"
            f"Разбан: {until.strftime('%d.%m.%Y %H:%M')}",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.reply(f"Ошибка: {e}")


@dp.message(Command("unban"))
async def unban_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    target = await resolve_target(message)
    if not target:
        return await message.reply("Укажи ID или @юзернейм.")
    try:
        await bot.unban_chat_member(message.chat.id, target, only_if_banned=True)
        bans.get(message.chat.id, {}).pop(target, None)
        await message.reply(f"✅ Разбанен <code>{target}</code>.", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"Ошибка: {e}")


@dp.message(Command("kick"))
async def kick_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    target = await resolve_target(message)
    if not target:
        return await message.reply("Укажи юзера или ID.")
    try:
        await bot.ban_chat_member(message.chat.id, target)
        await bot.unban_chat_member(message.chat.id, target)
        await message.reply(f"👢 Кикнут <code>{target}</code>.", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"Ошибка: {e}")


@dp.message(Command("mute"))
async def mute_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    target = await resolve_target(message)
    if not target:
        return await message.reply("Укажи юзера или ID.")
    dur = parse_duration(message.text) or timedelta(hours=1)
    until = datetime.now() + dur
    try:
        await bot.restrict_chat_member(
            message.chat.id, target,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
        await message.reply(
            f"🔇 Замучен <code>{target}</code> на {fmt_duration(dur)}.",
            parse_mode="HTML",
        )
    except Exception as e:
        await message.reply(f"Ошибка: {e}")


@dp.message(Command("unmute"))
async def unmute_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    target = await resolve_target(message)
    if not target:
        return await message.reply("Укажи юзера или ID.")
    try:
        await bot.restrict_chat_member(
            message.chat.id, target,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        await message.reply(f"🔊 Размучен <code>{target}</code>.", parse_mode="HTML")
    except Exception as e:
        await message.reply(f"Ошибка: {e}")


@dp.message(Command("warn"))
async def warn_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    target = await resolve_target(message)
    if not target:
        return await message.reply("Укажи юзера или ID.")
    cw = warns.setdefault(message.chat.id, {})
    cw[target] = cw.get(target, 0) + 1
    count = cw[target]
    if count >= WARN_LIMIT:
        until = datetime.now() + timedelta(hours=1)
        try:
            await bot.restrict_chat_member(
                message.chat.id, target,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            cw[target] = 0
            await message.reply(
                f"⚠️ <code>{target}</code> получил {WARN_LIMIT} варна → мут на 1 час.",
                parse_mode="HTML",
            )
        except Exception as e:
            await message.reply(f"Ошибка: {e}")
    else:
        await message.reply(
            f"⚠️ Варн <code>{target}</code>: {count}/{WARN_LIMIT}", parse_mode="HTML"
        )


@dp.message(Command("unwarn"))
async def unwarn_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    target = await resolve_target(message)
    if not target:
        return await message.reply("Укажи юзера или ID.")
    cw = warns.setdefault(message.chat.id, {})
    if cw.get(target, 0) > 0:
        cw[target] -= 1
    await message.reply(
        f"✅ Снят варн с <code>{target}</code>. Осталось: {cw.get(target, 0)}",
        parse_mode="HTML",
    )


@dp.message(Command("warns"))
async def warns_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message) or message.from_user.id
    count = warns.get(message.chat.id, {}).get(target, 0)
    await message.reply(
        f"Варны <code>{target}</code>: {count}/{WARN_LIMIT}", parse_mode="HTML"
    )


# ---------- Правила ----------
@dp.message(Command("setrules"))
async def setrules_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    text = message.text.partition(" ")[2].strip()
    if not text:
        return await message.reply("Напиши текст правил после команды.")
    rules_text[message.chat.id] = text
    await message.reply("✅ Правила обновлены.")


@dp.message(Command("rules"))
async def rules_cmd(message: Message):
    if message.chat.type == "private":
        return await message.answer(DEFAULT_RULES, parse_mode="HTML")
    await message.reply(rules_text.get(message.chat.id, DEFAULT_RULES), parse_mode="HTML")


# ---------- Управление ----------
@dp.message(Command("antimat"))
async def antimat_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        antimat_on[message.chat.id] = True
        await message.reply("✅ Антимат включён.")
    elif arg == "off":
        antimat_on[message.chat.id] = False
        await message.reply("❌ Антимат выключен.")
    else:
        state = "вкл" if antimat_on.get(message.chat.id) else "выкл"
        await message.reply(f"Антимат: {state}. /antimat on|off")


@dp.message(Command("antiflood"))
async def antiflood_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    arg = message.text.partition(" ")[2].strip().lower()
    if arg == "on":
        antiflood_on[message.chat.id] = True
        await message.reply("✅ Антифлуд включён.")
    elif arg == "off":
        antiflood_on[message.chat.id] = False
        await message.reply("❌ Антифлуд выключен.")
    else:
        state = "вкл" if antiflood_on.get(message.chat.id) else "выкл"
        await message.reply(f"Антифлуд: {state}. /antiflood on|off")


@dp.message(Command("pin"))
async def pin_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    if not message.reply_to_message:
        return await message.reply("Ответь реплаем на сообщение.")
    try:
        await bot.pin_chat_message(
            message.chat.id, message.reply_to_message.message_id
        )
        await message.reply("📌 Закреплено.")
    except Exception as e:
        await message.reply(f"Ошибка: {e}")


@dp.message(Command("purge"))
async def purge_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")
    m = re.search(r"(\d+)", message.text)
    n = int(m.group(1)) if m else 0
    if n < 1 or n > 100:
        return await message.reply("Укажи число от 1 до 100. Пример: /purge 10")
    deleted = 0
    for i in range(message.message_id, message.message_id - n - 1, -1):
        try:
            await bot.delete_message(message.chat.id, i)
            deleted += 1
        except Exception:
            pass
    msg = await message.answer(f"🧹 Удалено: {deleted}")
    await asyncio.sleep(3)
    try:
        await msg.delete()
    except Exception:
        pass


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if message.chat.type == "private":
        return
    cid = message.chat.id
    w = warns.get(cid, {})
    b = bans.get(cid, {})
    await message.reply(
        f"📊 <b>Статистика чата</b>\n"
        f"• Юзеров с варнами: {len(w)}\n"
        f"• Всего варнов: {sum(w.values())}\n"
        f"• Активных банов: {len(b)}\n"
        f"• Антимат: {'вкл' if antimat_on.get(cid) else 'выкл'}\n"
        f"• Антифлуд: {'вкл' if antiflood_on.get(cid) else 'выкл'}",
        parse_mode="HTML",
    )


@dp.message(Command("id"))
async def id_cmd(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        text = f"👤 {u.full_name}\n🆔 <code>{u.id}</code>"
        if u.username:
            text += f"\n🔗 @{u.username}"
        await message.reply(text, parse_mode="HTML")
    else:
        await message.reply(
            f"Твой ID: <code>{message.from_user.id}</code>", parse_mode="HTML"
        )


# ---------- Развлечения ----------
@dp.message(Command("dice"))
async def dice_cmd(message: Message):
    await message.answer_dice(emoji="🎲")


@dp.message(Command("coin"))
async def coin_cmd(message: Message):
    await message.answer(random.choice(["🪙 Орёл", "🪙 Решка"]))


@dp.message(Command("roll"))
async def roll_cmd(message: Message):
    m = re.search(r"(\d+)", message.text)
    n = int(m.group(1)) if m else 100
    if n < 2 or n > 1_000_000:
        return await message.reply("Число от 2 до 1000000.")
    await message.reply(f"🎯 {random.randint(1, n)}")


# ==================== АВТОМОДЕРАЦИЯ ====================
@dp.message(F.text)
async def automod(message: Message):
    if message.chat.type == "private":
        return
    # запоминаем username
    if message.from_user and message.from_user.username:
        known_users.setdefault(message.chat.id, {})[
            message.from_user.username.lower()
        ] = message.from_user.id

    if await is_admin(message.chat.id, message.from_user.id):
        return

    text = message.text or ""

    # антимат
    if antimat_on.get(message.chat.id):
        low = text.lower()
        if any(re.search(p, low) for p in BAD_WORDS):
            try:
                await message.delete()
            except Exception:
                pass
            await bot.send_message(
                message.chat.id, f"🚫 {message.from_user.mention}, мат запрещён!"
            )
            return

    # антифлуд
    if antiflood_on.get(message.chat.id):
        now = asyncio.get_event_loop().time()
        log = flood_log.setdefault(message.chat.id, {}).setdefault(
            message.from_user.id, []
        )
        log.append(now)
        log[:] = [t for t in log if now - t < FLOOD_WINDOW]
        if len(log) >= FLOOD_LIMIT:
            try:
                await message.delete()
            except Exception:
                pass
            until = datetime.now() + timedelta(minutes=5)
            try:
                await bot.restrict_chat_member(
                    message.chat.id, message.from_user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until,
                )
                await bot.send_message(
                    message.chat.id,
                    f"🚫 {message.from_user.mention} замучен на 5 минут за флуд.",
                )
            except Exception:
                pass
            log.clear()


# ==================== ПРИВЕТСТВИЕ ====================
@dp.message(F.new_chat_members)
async def welcome(message: Message):
    for user in message.new_chat_members:
        if user.id == bot.id:
            continue
        await message.answer(
            f"👋 Добро пожаловать, {user.mention}!\n"
            f"Прочитай /rules — там всё важное. Модерирую я — Jopa Менеджер 💫"
        )


@dp.message(F.left_chat_member)
async def goodbye(message: Message):
    u = message.left_chat_member
    if u and u.id != bot.id:
        await message.answer(f"👋 {u.full_name} покинул(а) чат.")


# ==================== ЗАПУСК ====================
async def main():
    logging.info("Jopa Менеджер запущен 🚀")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
