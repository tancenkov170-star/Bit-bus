import os
import re
import asyncio
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ChatPermissions
from aiogram.filters import CommandStart, Command
from aiogram.enums import ChatMemberStatus

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---------- Хранилище (в оперативке) ----------
# warns: {chat_id: {user_id: count}}
warns: dict[int, dict[int, int]] = {}
# bans: {chat_id: {user_id: datetime_until}}
bans: dict[int, dict[int, datetime]] = {}
# правила: {chat_id: str}
rules_text: dict[int, str] = {}
# антимат: {chat_id: bool}
antimat_on: dict[int, bool] = {}
# антифлуд: {chat_id: {user_id: [timestamps]}}
flood_log: dict[int, dict[int, list[float]]] = {}

DEFAULT_RULES = (
    "📜 <b>Правила чата</b>\n\n"
    "1. Уважай участников — без оскорблений и травли.\n"
    "2. Запрещён мат и нецензурная лексика.\n"
    "3. Без спама, флуда и рекламы.\n"
    "4. Не оффтопь — для этого есть отдельные темы.\n"
    "5. Запрещены NSFW-материалы и шок-контент.\n"
    "6. Не разжигай конфликты на почве религии, политики, нации.\n"
    "7. Слушай администрацию — её слово последнее.\n\n"
    "⚠️ Наказания: предупреждение → мут → кик → бан."
)

BAD_WORDS = [
    r"\bбля\w*", r"\bхуй\w*", r"\bпизд\w*", r"\bеба\w*", r"\bёб\w*",
    r"\bсук\w*", r"\bмуд\w*", r"\bнах\w*", r"\bпидор\w*", r"\bдолбо\w*",
]

WARN_LIMIT = 3  # сколько варнов до автомута


# ---------- Утилиты ----------
async def is_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except Exception:
        return False


async def resolve_target(message: Message) -> int | None:
    """Достаёт user_id из реплая, @username или числового ID."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id

    args = message.text.split()[1:] if message.text else []
    if not args:
        return None

    arg = args[0]
    if arg.startswith("@"):
        try:
            chat = await bot.get_chat(arg)
            return chat.id
        except Exception:
            return None
    if arg.lstrip("-").isdigit():
        return int(arg)
    return None


def parse_duration(text: str) -> timedelta | None:
    """'3d', '12h', '30m', '2w' → timedelta"""
    m = re.search(r"(\d+)\s*([smhdw])", text.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
    }[unit]


def fmt_duration(td: timedelta) -> str:
    s = int(td.total_seconds())
    if s >= 86400:
        return f"{s // 86400} д."
    if s >= 3600:
        return f"{s // 3600} ч."
    if s >= 60:
        return f"{s // 60} мин."
    return f"{s} сек."


# ---------- Базовые команды ----------
@dp.message(CommandStart())
async def start_cmd(message: Message):
    await message.answer(
        "Привет! Я Iris — менеджер чата 💫\n"
        "Добавь меня в группу и выдай права администратора.\n"
        "В личке: /help"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "🛡 <b>Команды модерации</b> (только для админов):\n\n"
        "<b>Наказания:</b>\n"
        "/ban [юзер/id] [срок] — забанить (напр. <code>/ban @user 3d</code>)\n"
        "/unban [id] — разбанить\n"
        "/kick [юзер/id] — кикнуть\n"
        "/mute [юзер/id] [срок] — замутить\n"
        "/unmute [юзер/id] — размутить\n"
        "/warn [юзер/id] — предупреждение\n"
        "/unwarn [юзер/id] — снять варн\n"
        "/warns [юзер/id] — посмотреть варны\n\n"
        "<b>Управление:</b>\n"
        "/setrules [текст] — задать правила\n"
        "/rules — показать правила\n"
        "/antimat on|off — фильтр мата\n"
        "/pin — закрепить сообщение (реплай)\n"
        "/purge [N] — удалить N сообщений\n"
        "/stats — статистика чата\n\n"
        "💡 Можно отвечать реплаем на сообщение — тогда цель берётся оттуда.",
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
        return await message.reply("Укажи юзера или ID. Пример: /ban @user 3d")

    # срок
    dur = parse_duration(message.text)
    if dur is None:
        dur = timedelta(days=1)

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
            message.chat.id,
            target,
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
            message.chat.id,
            target,
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

    chat_warns = warns.setdefault(message.chat.id, {})
    chat_warns[target] = chat_warns.get(target, 0) + 1
    count = chat_warns[target]

    if count >= WARN_LIMIT:
        until = datetime.now() + timedelta(hours=1)
        try:
            await bot.restrict_chat_member(
                message.chat.id,
                target,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            chat_warns[target] = 0
            await message.reply(
                f"⚠️ <code>{target}</code> получил {WARN_LIMIT} варна → мут на 1 час.",
                parse_mode="HTML",
            )
        except Exception as e:
            await message.reply(f"Ошибка: {e}")
    else:
        await message.reply(
            f"⚠️ Варн <code>{target}</code>: {count}/{WARN_LIMIT}",
            parse_mode="HTML",
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

    chat_warns = warns.setdefault(message.chat.id, {})
    if chat_warns.get(target, 0) > 0:
        chat_warns[target] -= 1
    await message.reply(f"✅ Снят варн с <code>{target}</code>. Осталось: {chat_warns.get(target, 0)}", parse_mode="HTML")


@dp.message(Command("warns"))
async def warns_cmd(message: Message):
    if message.chat.type == "private":
        return
    target = await resolve_target(message) or message.from_user.id
    count = warns.get(message.chat.id, {}).get(target, 0)
    await message.reply(f"Варны <code>{target}</code>: {count}/{WARN_LIMIT}", parse_mode="HTML")


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

    text = rules_text.get(message.chat.id, DEFAULT_RULES)
    await message.reply(text, parse_mode="HTML")


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
        await message.reply(f"Антимат: {state}. Используй /antimat on|off")


@dp.message(Command("pin"))
async def pin_cmd(message: Message):
    if message.chat.type == "private":
        return
    if not await is_admin(message.chat.id, message.from_user.id):
        return await message.reply("⛔ Только для админов.")

    if not message.reply_to_message:
        return await message.reply("Ответь реплаем на сообщение.")

    try:
        await bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
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

    # удаляем от текущего сообщения назад
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
    chat_id = message.chat.id
    w = warns.get(chat_id, {})
    b = bans.get(chat_id, {})
    total_warns = sum(w.values())
    await message.reply(
        f"📊 <b>Статистика чата</b>\n"
        f"• Пользователей с варнами: {len(w)}\n"
        f"• Всего варнов: {total_warns}\n"
        f"• Активных банов: {len(b)}\n"
        f"• Антимат: {'вкл' if antimat_on.get(chat_id) else 'выкл'}",
        parse_mode="HTML",
    )


# ---------- Автомодерация: антимат + антифлуд ----------
@dp.message(F.text)
async def automod(message: Message):
    if message.chat.type == "private":
        return

    # пропускаем админов
    if await is_admin(message.chat.id, message.from_user.id):
        return

    text = message.text or ""

    # --- антимат ---
    if antimat_on.get(message.chat.id):
        low = text.lower()
        if any(re.search(p, low) for p in BAD_WORDS):
            try:
                await message.delete()
            except Exception:
                pass
            await bot.send_message(
                message.chat.id,
                f"🚫 {message.from_user.mention}, мат запрещён!",
            )
            return

    # --- антифлуд (5 сообщений за 5 сек) ---
    now = asyncio.get_event_loop().time()
    user_log = flood_log.setdefault(message.chat.id, {}).setdefault(message.from_user.id, [])
    user_log.append(now)
    user_log[:] = [t for t in user_log if now - t < 5]

    if len(user_log) >= 5:
        try:
            await message.delete()
        except Exception:
            pass
        until = datetime.now() + timedelta(minutes=5)
        try:
            await bot.restrict_chat_member(
                message.chat.id,
                message.from_user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await bot.send_message(
                message.chat.id,
                f"🚫 {message.from_user.mention} замучен на 5 минут за флуд.",
            )
        except Exception:
            pass
        user_log.clear()


# ---------- Приветствие новых ----------
@dp.message(F.new_chat_members)
async def welcome(message: Message):
    for user in message.new_chat_members:
        if user.id == bot.id:
            continue
        await message.answer(
            f"👋 Добро пожаловать, {user.mention}!\n"
            f"Прочитай /rules — там всё важное."
        )


@dp.message(F.left_chat_member)
async def goodbye(message: Message):
    user = message.left_chat_member
    if user and user.id != bot.id:
        await message.answer(f"👋 {user.full_name} покинул(а) чат.")


async def main():
    logging.info("Iris Chat Manager запущена 🚀")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
