"""Real aiogram/FastAPI boundaries with synthetic updates and no external I/O."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('OPENAI_API_KEY', 'offline-placeholder')
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
import httpx
import app
import handlers

class RuntimeContactTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Bot(token='123456:synthetic-placeholder')
        self.storage = MemoryStorage()
        self.old_storage = app.dispatcher.fsm.storage
        app.dispatcher.fsm.storage = self.storage
        self.state = app.dispatcher.fsm.get_context(bot=self.bot, chat_id=42, user_id=42)
        await self.state.set_state(handlers.Interview.waiting_contact)
        self.transport = patch.object(self.bot.session, 'make_request', new_callable=AsyncMock)
        self.send = self.transport.start()
        self.crm_patch = patch.object(handlers.amo, 'find_or_create_lead', new_callable=AsyncMock)
        self.crm = self.crm_patch.start()
        self.crm.return_value = 123
        self.gpt_patch = patch.object(handlers, 'ask_hr_gpt', new_callable=AsyncMock)
        self.gpt = self.gpt_patch.start()
        self.gpt.return_value = 'Synthetic question'
        for middleware in app.dispatcher.message.middleware:
            if hasattr(middleware, 'user_timestamps'):
                middleware.user_timestamps.clear()

    async def asyncTearDown(self):
        self.transport.stop()
        self.crm_patch.stop()
        self.gpt_patch.stop()
        app.dispatcher.fsm.storage = self.old_storage
        await self.storage.close()
        await self.bot.session.close()

    async def send_contact(self, owner):
        contact = {'phone_number': '+0000000000', 'first_name': 'Synthetic'}
        if owner is not None:
            contact['user_id'] = owner
        update = Update.model_validate({'update_id': 1, 'message': {
            'message_id': 1, 'date': 0, 'chat': {'id': 42, 'type': 'private'},
            'from': {'id': 42, 'is_bot': False, 'first_name': 'Synthetic'},
            'contact': contact,
        }})
        await app.dispatcher.feed_update(self.bot, update)

    async def test_foreign_contact_is_rejected_by_real_dispatch(self):
        await self.send_contact(43)
        self.crm.assert_not_awaited()
        self.gpt.assert_not_awaited()
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual(await self.state.get_state(), handlers.Interview.waiting_contact.state)
        self.send.assert_awaited_once()

    async def test_ownerless_contact_is_rejected_by_real_dispatch(self):
        await self.send_contact(None)
        self.crm.assert_not_awaited()
        self.gpt.assert_not_awaited()
        self.assertEqual(await self.state.get_data(), {})

    async def test_own_contact_keeps_existing_flow(self):
        await self.send_contact(42)
        self.crm.assert_awaited_once()
        self.gpt.assert_awaited_once()
        self.assertEqual((await self.state.get_data())['phone'], '+0000000000')
        self.assertEqual(await self.state.get_state(), handlers.Interview.interviewing.state)

class WebhookBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_wrong_secret_never_dispatches(self):
        with patch.dict(os.environ, {'TELEGRAM_WEBHOOK_SECRET': 'synthetic-secret'}), patch.object(app.dispatcher, 'feed_update', new_callable=AsyncMock) as feed:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url='http://offline') as client:
                for headers in ({}, {'X-Telegram-Bot-Api-Secret-Token': 'wrong'}):
                    response = await client.post('/api/telegram', json={'update_id': 1}, headers=headers)
                    self.assertEqual(response.status_code, 401)
            feed.assert_not_awaited()

    async def test_valid_secret_dispatches_synthetic_update(self):
        with patch.dict(os.environ, {'TELEGRAM_WEBHOOK_SECRET': 'synthetic-secret', 'TELEGRAM_BOT_TOKEN': '123456:synthetic-placeholder'}), patch.object(app.dispatcher, 'feed_update', new_callable=AsyncMock) as feed:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url='http://offline') as client:
                response = await client.post('/api/telegram', json={'update_id': 1}, headers={'X-Telegram-Bot-Api-Secret-Token': 'synthetic-secret'})
            self.assertEqual(response.status_code, 200)
            feed.assert_awaited_once()
