import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN
from db import init_db, close_db
from middlewares.subscription import SubscriptionMiddleware
from handlers import (
    start, help as help_h, moderation, roles, settings,
    info, fun, events, automod, reports,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


async def main():
    await init_db()
    log.info("БД инициализирована")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(SubscriptionMiddleware())

    # порядок важен!
    dp.include_router(start.router)
    dp.include_router(help_h.router)
    dp.include_router(moderation.router)
    dp.include_router(roles.router)
    dp.include_router(settings.router)
    dp.include_router(info.router)
    dp.include_router(fun.router)
    dp.include_router(reports.router)
    dp.include_router(events.router)
    dp.include_router(automod.router)   # В КОНЦЕ

    log.info("Jopa Менеджер запущен 🚀")
    try:
        await dp.start_polling(bot)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
