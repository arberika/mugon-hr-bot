"""
MUGON.CLUB HR Interview Bot — bot.py
Main entry point. Uses aiogram 3.x + OpenAI GPT-4 + AmoCRM API.
"""
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from dotenv import load_dotenv

from handlers import router
from middleware import ThrottlingMiddleware, VerificationMiddleware

load_dotenv()

logging.basicConfig(
      level=logging.INFO,
      format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
      bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
      storage = RedisStorage.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
      dp = Dispatcher(storage=storage)

    dp.include_router(router)
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(VerificationMiddleware())

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("MUGON HR Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
      asyncio.run(main())
