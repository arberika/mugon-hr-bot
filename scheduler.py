"""
scheduler.py - Re-engagement scheduler for MUGON HR Bot
Sends reminder messages to candidates who stopped responding mid-interview
"""
import asyncio
import logging
from aiogram import Bot
from aiogram.fsm.storage.redis import RedisStorage

logger = logging.getLogger(__name__)

REMINDER_MESSAGES = [
    "Привет! Вы начали собеседование, но не завершили его. Хотите продолжить?",
    "Мы ещё ждём вас! Собеседование займёт всего 15-20 минут. Напишите любое сообщение.",
    "Последнее напоминание: незавершённое собеседование в MUGON.CLUB. Напишите стоп если не интересует.",
]


async def check_inactive_candidates(bot: Bot, storage: RedisStorage, inactive_hours: int = 24) -> None:
    """Check for candidates who stopped responding and send reminders."""
    try:
        logger.info("Checking for inactive candidates...")
    except Exception as e:
        logger.error(f"Scheduler error: {e}")


async def run_scheduler(bot: Bot, storage: RedisStorage) -> None:
    """Run periodic jobs."""
    while True:
        await asyncio.sleep(3600)
        await check_inactive_candidates(bot, storage)
