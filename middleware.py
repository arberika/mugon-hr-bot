"""
middleware.py - MUGON HR Bot middlewares
Throttling: prevent token waste from empty dialogs
Verification: require phone number before proceeding
"""
import logging
from typing import Callable, Any
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from aiogram.fsm.context import FSMContext
from collections import defaultdict
import time

logger = logging.getLogger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    """Rate limiting to prevent token waste from spam."""

    def __init__(self, rate_limit: float = 1.0, burst: int = 5):
        self.rate_limit = rate_limit  # seconds between messages
        self.burst = burst             # allowed burst
        self.user_timestamps = defaultdict(list)

    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)

        now = time.time()
        timestamps = self.user_timestamps[user_id]
        # Remove old timestamps
        self.user_timestamps[user_id] = [t for t in timestamps if now - t < 60]

        if len(self.user_timestamps[user_id]) >= self.burst:
            await event.answer(
                "⏳ Пожалуйста, не торопитесь. Отвечайте вдумчиво."
            )
            return

        self.user_timestamps[user_id].append(now)
        return await handler(event, data)


class VerificationMiddleware(BaseMiddleware):
    """Ensure phone verification before deep interview interaction."""

    EXEMPT_COMMANDS = {"/start", "/help"}

    async def __call__(self, handler: Callable, event: TelegramObject, data: dict) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        # Allow contact sharing and commands through
        if event.contact or (event.text and event.text.startswith("/")):
            return await handler(event, data)

        # Check FSM state for phone verification
        state: FSMContext = data.get("state")
        if state:
            current_state = await state.get_state()
            state_data = await state.get_data()

            # States that require verification
            restricted_states = [
                "Interview:interviewing",
                "Interview:waiting_resume_file",
            ]

            if current_state in restricted_states:
                if not state_data.get("phone"):
                    await event.answer(
                        "🔐 Для продолжения необходима верификация номера телефона.\n"
                        "Нажмите /start чтобы начать."
                    )
                    return

        return await handler(event, data)
