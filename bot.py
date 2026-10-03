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

# Храним мут по chat_id И по user_id (на случай, если chat_id не совпадает)
muted_chats: dict[int, datetime] = {}
muted_users: dict[int, datetime] = {}

MAX_SPAM = 100
SPAM_INTERVAL = 0.03  # ~30 сообщений в секунду, безопасно


def is_muted(chat_id: int, user_id: int | None) -> bool:
    now = datetime.utcnow()
    if chat_id in muted_chats:
        if now < muted_chats[chat_id]:
            return True
        del muted_chats[chat_id]
    if user_id and user_id in muted_users:
        if now < muted_users[user_id]:
            return True
        del muted_users[user_id]
    return False


@dp.business_connection()
async def on_business_connection(conn: BusinessConnection):
    print(f"[BC] id={conn.id} user={conn.user.id} can_reply={conn.can_reply}")


@dp.business_message()
async def on_business_message(message: Message):
    conn_id = message.business_connection_id
    chat_id = message.chat.id
    from_user = message.from_user
    from_id = from_user.id if from_user else None
    text = (message.text or "").strip()

    # ==== ОТЛАДКА ====
    print(f"[MSG] chat={chat_id} from={from_id} text={text!r} muted={is_muted(chat_id, from_id)}")

    # ==== .mute N ====
    m = re.match(r"^\.mute\s+(\d+)\s*$", text)
    if m:
        minutes = int(m.group(1))
        until = datetime.utcnow() + timedelta(minutes=minutes)
        muted_chats[chat_id] = until
        # Также замутим по user_id собеседника, если можем его вычислить.
        # В бизнес-чате собеседник — это второй участник. Пробуем получить его.
        # Обычно from_user == владелец business, а собеседник — второй.
        # Проще: сохраняем и по chat_id, и по собеседнику через отдельный вызов,
        # но пока ограничимся chat_id + попыткой по from_id собеседника ниже.
        await bot.send_message(
            chat_id=chat_id,
            text=f"🔇 Мут на {minutes} мин. Его сообщения удаляются.",
            business_connection_id=conn_id,
        )
        return

    # ==== .unmute ====
    if text == ".unmute":
        muted_chats.pop(chat_id, None)
        if from_id:
            muted_users.pop(from_id, None)
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
        sent = 0
        for i in range(count):
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=payload,
                    business_connection_id=conn_id,
                )
                sent += 1
            except Exception as e:
                print(f"[SPAM] error on {i+1}: {e}")
                break
            await asyncio.sleep(SPAM_INTERVAL)
        print(f"[SPAM] done: {sent}/{count}")
        return

    # ==== УДАЛЕНИЕ ЗАМУЧЕННЫХ ====
    # Удаляем всё, что НЕ является командой (не начинается с точки)
    if text.startswith("."):
        return

    if is_muted(chat_id, from_id):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message.message_id)
            print(f"[DEL] ✅ удалено msg={message.message_id} chat={chat_id}")
        except Exception as e:
            print(f"[DEL] ❌ ошибка: {e}")


async def main():
    me = await bot.get_me()
    print(f"[START] Запущен как @{me.username}")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
