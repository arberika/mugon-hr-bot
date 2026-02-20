"""
scheduler.py - Re-engagement scheduler for MUGON HR Bot
Scans Redis for inactive interview sessions and sends tiered reminders.

FIXES:
  - reminders_sent: actually updated in Redis after each send (was commented out)
  - last_activity=0 guard: skip sessions with no recorded activity
  - Redis connection properly closed after each cycle
"""
import asyncio
import json
import logging
import time
from aiogram import Bot
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REMINDER_DELAYS_HOURS = [24, 48, 72]   # When to send reminders (hours of inactivity)

REMINDER_MESSAGES = [
    (
        "Привет! Помню, вы начали собеседование в MUGON.CLUB. "
        "Хотите продолжить? Просто напишите мне"
    ),
    (
        "Мы всё ещё заинтересованы в вашей кандидатуре! "
        "Собеседование займёт 15-20 минут. Напишите любое сообщение — "
        "продолжим с того места где остановились."
    ),
    (
        "Последнее напоминание от MUGON.CLUB. "
        "Ваше незавершённое собеседование ждёт. "
        "Если передумали — напишите стоп и мы не будем беспокоить. Удачи!"
    ),
]

STALE_DAYS = 7  # Delete sessions older than 7 days


async def get_all_interview_sessions(redis_url: str) -> list[dict]:
    """Scan Redis for all Interview:interviewing FSM states."""
    sessions = []
    try:
        r = await aioredis.from_url(redis_url)
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="fsm:*:state", count=100)
            for key in keys:
                state_val = await r.get(key)
                if state_val and b"interviewing" in state_val:
                    data_key = key.replace(b":state", b":data")
                    data_val = await r.get(data_key)
                    if data_val:
                        try:
                            data = json.loads(data_val)
                            parts = key.decode().split(":")
                            if len(parts) >= 4:
                                user_id = int(parts[3])
                                sessions.append({
                                    "user_id":        user_id,
                                    "data":           data,
                                    "key":            key.decode(),
                                    "data_key":       data_key.decode(),
                                    "last_activity":  data.get("last_activity", 0),
                                    "reminders_sent": data.get("reminders_sent", 0),
                                })
                        except (json.JSONDecodeError, ValueError, IndexError) as e:
                            logger.warning(f"Could not parse session key {key}: {e}")
            if cursor == 0:
                break
        await r.aclose()
    except Exception as e:
        logger.error(f"Redis scan error: {e}")
    return sessions


async def check_inactive_candidates(bot: Bot, redis_url: str) -> None:
    """Check for inactive candidates and send tiered reminders."""
    now      = time.time()
    sessions = await get_all_interview_sessions(redis_url)

    if not sessions:
        return

    try:
        r = await aioredis.from_url(redis_url)
    except Exception as e:
        logger.error(f"Redis connect error in check_inactive_candidates: {e}")
        return

    for session in sessions:
        user_id         = session["user_id"]
        last_activity   = session["last_activity"]
        reminders_sent  = session["reminders_sent"]
        data_key        = session["data_key"]

        # FIX 3a: skip sessions where last_activity was never recorded
        if not last_activity or last_activity == 0:
            logger.debug(f"Skipping user {user_id}: no last_activity recorded yet")
            continue

        hours_inactive = (now - last_activity) / 3600

        # Determine which reminder to send (if any)
        reminder_idx = None
        for i, delay_hours in enumerate(REMINDER_DELAYS_HOURS):
            if hours_inactive >= delay_hours and reminders_sent <= i:
                reminder_idx = i
                break

        if reminder_idx is not None:
            try:
                await bot.send_message(user_id, REMINDER_MESSAGES[reminder_idx])
                logger.info(f"Sent reminder #{reminder_idx + 1} to user {user_id} "
                            f"({hours_inactive:.1f}h inactive)")

                # FIX 3b: update reminders_sent in Redis (was a comment before)
                raw = await r.get(data_key)
                if raw:
                    try:
                        data = json.loads(raw)
                        data["reminders_sent"] = reminder_idx + 1
                        await r.set(data_key, json.dumps(data))
                        logger.info(f"Updated reminders_sent={reminder_idx + 1} for user {user_id}")
                    except Exception as update_err:
                        logger.error(f"Failed to update reminders_sent for {user_id}: {update_err}")

            except Exception as send_err:
                logger.warning(f"Could not send reminder to {user_id}: {send_err}")

        # Log stale sessions (older than STALE_DAYS)
        if hours_inactive >= STALE_DAYS * 24:
            logger.info(
                f"Stale interview session: user {user_id}, "
                f"{hours_inactive:.0f}h inactive, {reminders_sent} reminders sent"
            )

    await r.aclose()


async def run_scheduler(bot: Bot, redis_url: str) -> None:
    """Background task: run periodic re-engagement checks every hour."""
    logger.info("Scheduler started — checking inactive candidates every hour")
    while True:
        try:
            await check_inactive_candidates(bot, redis_url)
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")
        await asyncio.sleep(3600)
