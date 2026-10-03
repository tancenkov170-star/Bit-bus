import asyncio
import re
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessConnection

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# {chat_id: {"mute_until": datetime|None}}
state: dict[int, dict] = {}
MAX_SPAM = 100
SPAM_INTERVAL = 1.0


def get_state(chat_id: int) -> dict:
    return state.setdefault(chat_id, {"mute_until": None})


def is_muted(chat_id: int) -> bool:
    st = get_state(chat_id)
    if st["mute_until"] is None:
        return False
    if datetime.utcnow() >= st["mute_until"]:
        st["mute_until"] = None
        return False
    return True


# ==== ПОДКЛЮЧЕНИЕ BUSINESS ====
@dp.business_connection()
async def on_business_connection(conn: BusinessConnection):
    print(f"Business connection: id={conn.id}, user={conn.user.id}, can_reply={conn.can_reply}")


# ==== ВСЕ BUSINESS-СООБЩЕНИЯ (и твои, и собеседника) ====
@dp.business_message()
async def on_business_message(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    text = (message.text or "").strip()

    # Определяем, кто написал — ты (владелец) или собеседник
    # Владелец — это тот, кому принадлежит business connection.
    # В business_message поле from_user = отправитель.
    # Твои собственные исходящие сообщения приходят с from_user = твой id,
    # но точнее проверять через business_connection.

    # ==== КОМАНДА .mute N ====
    m = re.match(r"^\.mute\s+(\d+)\s*$", text)
    if m:
        minutes = int(m.group(1))
        get_state(chat_id)["mute_until"] = datetime.utcnow() + timedelta(minutes=minutes)
        await bot.send_message(
            chat_id=chat_id,
            text=f"🔇 Мут на {minutes} мин. Его сообщения удаляются.",
            business_connection_id=conn_id,
        )
        return

    # ==== .unmute ====
    if text == ".unmute":
        get_state(chat_id)["mute_until"] = None
        await bot.send_message(
            chat_id=chat_id,
            text="🔊 Мут снят.",
            business_connection_id=conn_id,
        )
        return

    # ==== .spam N текст ====
    m = re.match(r"^\.spam\s+(\d+)\s+(.+)$", text, re.DOTALL)
    if m:
        count = min(int(m.group(1)), MAX_SPAM)
        payload = m.group(2)
        for _ in range(count):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=payload,
                    business_connection_id=conn_id,
                )
            except Exception as e:
                print(f"spam error: {e}")
                break
            await asyncio.sleep(SPAM_INTERVAL)
        return

    # ==== ЕСЛИ ЧАТ В МУТЕ — УДАЛЯЕМ ВХОДЯЩИЕ ОТ СОБЕСЕДНИКА ====
    if is_muted(chat_id):
        # Удаляем только сообщения НЕ от владельца business-аккаунта
        # Проверка простая: если сообщение не команда и не наше собственное —
        # пытаемся удалить.
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            print(f"Удалено сообщение {message.message_id} в чате {chat_id}")
        except Exception as e:
            print(f"delete error: {e}")


async def main():
    me = await bot.get_me()
    print(f"Запущен как @{me.username}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())