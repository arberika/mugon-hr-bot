"""
scheduler.py - Re-engagement scheduler for MUGON HR Bot
Scans Redis for inactive interview sessions and sends one neutral reminder.

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

REMINDER_DELAYS_HOURS = [48]   # One neutral reminder after two days of inactivity

REMINDER_MESSAGES = [
    (
        "Вы начали заполнять проектный профиль MUGON.CLUB. Если захотите, можно "
        "продолжить с того же места. Если профиль сейчас не нужен — ничего делать не требуется."
    ),
]

STALE_DAYS = 7  # Delete sessions older than 7 days


async def get_all_interview_sessions(redis_url: str) -> list[dict]:
    """Read private sessions using aiogram's default fsm:chat:user:state keys."""
    sessions = []
    r = None
    try:
        r = aioredis.from_url(redis_url)
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="fsm:*:state", count=100)
            for key in keys:
                decoded = key.decode() if isinstance(key, bytes) else key
                parts = decoded.split(":")
                # Only default private-chat keys; never infer recipients from extra parts.
                if len(parts) != 4 or parts[0] != "fsm" or parts[3] != "state":
                    continue
                try:
                    chat_id, user_id = int(parts[1]), int(parts[2])
                    if chat_id != user_id or user_id <= 0:
                        continue
                    state_val = await r.get(key)
                    if state_val not in (b"Interview:interviewing", "Interview:interviewing"):
                        continue
                    data_key = decoded.removesuffix(":state") + ":data"
                    data_val = await r.get(data_key)
                    if data_val:
                        data = json.loads(data_val)
                        sessions.append({
                            "user_id": user_id, "key": decoded, "data_key": data_key,
                            "last_activity": data.get("last_activity", 0),
                            "reminders_sent": data.get("reminders_sent", 0),
                        })
                except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
                    logger.warning("Skipping invalid interview session")
            if cursor == 0:
                break
    except Exception:
        logger.exception("Redis scan error")
    finally:
        if r is not None:
            await r.aclose()
    return sessions


async def check_inactive_candidates(bot: Bot, redis_url: str) -> None:
    """Make at most one reminder attempt, serialized with the user's updates."""
    now = time.time()
    sessions = await get_all_interview_sessions(redis_url)
    if not sessions:
        return
    r = aioredis.from_url(redis_url)
    try:
        for session in sessions:
            last_activity = session["last_activity"]
            if not last_activity or (now - last_activity) / 3600 < 48 or session["reminders_sent"]:
                continue
            try:
                # Same key and lock implementation as RedisEventIsolation in app/bot.
                lock_key = session["key"].removesuffix(":state") + ":lock"
                async with r.lock(name=lock_key, timeout=300, blocking_timeout=1):
                    current_state = await r.get(session["key"])
                    raw = await r.get(session["data_key"])
                    if current_state not in (b"Interview:interviewing", "Interview:interviewing") or not raw:
                        continue
                    data = json.loads(raw)
                    if (data.get("reminders_sent", 0)
                            or data.get("last_activity") != last_activity
                            or data.get("finalized")):
                        continue
                    # Reserve before the external call. An uncertain send is never retried.
                    data["reminders_sent"] = 1
                    await r.set(session["data_key"], json.dumps(data))
                    await bot.send_message(session["user_id"], REMINDER_MESSAGES[0])
                    logger.info("Sent the profile reminder")
            except Exception:
                logger.exception("Reminder attempt skipped or failed")
    finally:
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
