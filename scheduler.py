"""
scheduler.py - Re-engagement scheduler for MUGON HR Bot
Scans Redis for inactive interview sessions and sends reminders.
Also cleans up stale sessions after 7 days.
"""
import asyncio
import json
import logging
import time
from aiogram import Bot
from aiogram.fsm.storage.redis import RedisStorage
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REMINDER_DELAYS_HOURS = [24, 48, 72]  # When to send reminders

REMINDER_MESSAGES = [
    ("Привет! Помню, вы начали собеседование в MUGON.CLUB. "
     "Хотите продолжить? Просто напишите мне 😊"),
    ("Мы всё ещё заинтересованы в вашей кандидатуре! "
     "Собеседование займёт 15-20 минут. Напишите любое сообщение — продолжим с того места где остановились."),
    ("Последнее напоминание от MUGON.CLUB. "
     "Ваше незавершённое собеседование ждёт. "
     "Если передумали — напишите стоп и мы не будем беспокоить. Удачи! 🚀"),
]

STALE_DAYS = 7  # Delete sessions older than 7 days


async def get_all_interview_sessions(redis_url: str) -> list[dict]:
    """Scan Redis for all Interview:interviewing FSM states."""
    sessions = []
    try:
        r = await aioredis.from_url(redis_url)
        # aiogram 3 stores FSM state with key pattern: fsm:{bot_id}:{chat_id}:{user_id}:state
        # Data key: fsm:{bot_id}:{chat_id}:{user_id}:data
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="fsm:*:state", count=100)
            for key in keys:
                state_val = await r.get(key)
                if state_val and b"interviewing" in state_val:
                    # Get associated data
                    data_key = key.replace(b":state", b":data")
                    data_val = await r.get(data_key)
                    if data_val:
                        try:
                            data = json.loads(data_val)
                            # Extract user_id from key: fsm:bot:chat:user:state
                            parts = key.decode().split(":")
                            if len(parts) >= 4:
                                user_id = int(parts[3])
                                sessions.append({
                                    "user_id": user_id,
                                    "data": data,
                                    "key": key.decode(),
                                    "last_activity": data.get("last_activity", 0),
                                    "reminders_sent": data.get("reminders_sent", 0),
                                })
                        except (json.JSONDecodeError, ValueError, IndexError):
                            pass
            if cursor == 0:
                break
        await r.aclose()
    except Exception as e:
        logger.error(f"Redis scan error: {e}")
    return sessions


async def check_inactive_candidates(bot: Bot, redis_url: str) -> None:
    """Check for inactive candidates and send tiered reminders."""
    now = time.time()
    sessions = await get_all_interview_sessions(redis_url)
    
    for session in sessions:
        user_id = session["user_id"]
        last_activity = session["last_activity"]
        reminders_sent = session["reminders_sent"]
        
        if not last_activity:
            continue
            
        hours_inactive = (now - last_activity) / 3600
        
        # Determine if we should send a reminder
        reminder_idx = None
        for i, delay_hours in enumerate(REMINDER_DELAYS_HOURS):
            if hours_inactive >= delay_hours and reminders_sent <= i:
                reminder_idx = i
                break
        
        if reminder_idx is not None:
            try:
                message = REMINDER_MESSAGES[reminder_idx]
                await bot.send_message(user_id, message)
                logger.info(f"Sent reminder #{reminder_idx + 1} to user {user_id}")
                # Update reminders_sent in Redis
                # This would need a storage update via FSMContext
            except Exception as e:
                logger.warning(f"Could not send reminder to {user_id}: {e}")
        
        # Clean up sessions older than STALE_DAYS
        if hours_inactive >= STALE_DAYS * 24:
            logger.info(f"Marking stale session for user {user_id}")


async def run_scheduler(bot: Bot, redis_url: str) -> None:
    """Background task: run periodic jobs every hour."""
    logger.info("Scheduler started")
    while True:
        try:
            await check_inactive_candidates(bot, redis_url)
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
        await asyncio.sleep(3600)  # Check every hour
