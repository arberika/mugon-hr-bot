"""Vercel FastAPI entrypoint for the MUGON Telegram webhook."""

import os
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request

from handlers import router
from middleware import ThrottlingMiddleware, VerificationMiddleware


app = FastAPI(title="MUGON HR Bot", docs_url=None, redoc_url=None)
storage = RedisStorage.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
dispatcher = Dispatcher(storage=storage)
dispatcher.include_router(router)
dispatcher.message.middleware(ThrottlingMiddleware(rate_limit=1.0, burst=5))
dispatcher.message.middleware(VerificationMiddleware())


@app.get("/health")
async def health():
    return {
        "ok": True,
        "crm": "adminwowericom.kommo.com",
        "pipeline_id": int(os.environ.get("AMO_PIPELINE_ID", "14089467")),
        "status_id": int(os.environ.get("AMO_STATUS_NEW", "108772599")),
    }


@app.post("/api/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
    if not expected_secret or x_telegram_bot_api_secret_token != expected_secret:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    try:
        update = Update.model_validate(await request.json(), context={"bot": bot})
        await dispatcher.feed_update(bot, update)
    finally:
        await bot.session.close()
    return {"ok": True}
