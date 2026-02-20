"""
bot.py - MUGON HR Bot Entry Point
Aiogram 3.x + Redis FSM storage + background scheduler
"""
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from dotenv import load_dotenv

from handlers import router
from middleware import ThrottlingMiddleware, VerificationMiddleware
from scheduler import run_scheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


async def main():
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    storage = RedisStorage.from_url(redis_url)
    dp = Dispatcher(storage=storage)

    dp.include_router(router)
    dp.message.middleware(ThrottlingMiddleware(rate_limit=1.0, burst=5))
    dp.message.middleware(VerificationMiddleware())

    # Remove existing webhook (we use polling)
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("MUGON HR Bot starting in polling mode...")

    # Start background scheduler
    asyncio.create_task(run_scheduler(bot, redis_url))

    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()
        logger.info("MUGON HR Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
