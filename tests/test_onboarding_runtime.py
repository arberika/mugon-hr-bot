"""Actual aiogram dispatcher coverage; every external boundary is mocked."""
import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("OPENAI_API_KEY", "offline-placeholder")
from aiogram import Bot
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import Update
import app
import handlers
import notifier
import scheduler
from onboarding_copy import ACTION_CLUB, ACTION_MEETUP, ACTION_PROFILE


class OnboardingRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Bot(token="123456:synthetic-placeholder")
        self.storage = MemoryStorage()
        self.old_isolation = app.dispatcher.fsm.events_isolation
        app.dispatcher.fsm.events_isolation = SimpleEventIsolation()
        self.old_storage = app.dispatcher.fsm.storage
        app.dispatcher.fsm.storage = self.storage
        self.state = app.dispatcher.fsm.get_context(bot=self.bot, chat_id=42, user_id=42)
        self.patches = [
            patch.object(self.bot.session, "make_request", new_callable=AsyncMock),
            patch.object(handlers.amo, "find_or_create_lead", new_callable=AsyncMock),
            patch.object(handlers, "ask_hr_gpt", new_callable=AsyncMock),
            patch.object(handlers, "generate_ai_resume", new_callable=AsyncMock),
            patch.object(handlers.amo, "update_lead_fields", new_callable=AsyncMock),
            patch.object(handlers, "notify_ceo_pm", new_callable=AsyncMock),
        ]
        self.send, self.crm, self.gpt, self.resume, self.save, self.notify = [p.start() for p in self.patches]
        self.crm.return_value = 123
        self.gpt.return_value = "Synthetic question"
        self.resume.return_value = {"total_score": None, "ai_summary": "Synthetic summary"}
        self.notify.return_value = {"delivered": [], "failed": []}

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        await app.dispatcher.fsm.events_isolation.close()
        app.dispatcher.fsm.events_isolation = self.old_isolation
        app.dispatcher.fsm.storage = self.old_storage
        await self.storage.close()
        await self.bot.session.close()

    async def feed(self, text=None, **extra):
        for middleware in app.dispatcher.message.middleware:
            if hasattr(middleware, "user_timestamps"):
                middleware.user_timestamps.clear()
        message = {"message_id": 1, "date": 0, "chat": {"id": 42, "type": "private"},
                   "from": {"id": 42, "is_bot": False, "first_name": "Synthetic"}, **extra}
        if text is not None:
            message["text"] = text
        if text and text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
        await app.dispatcher.feed_update(self.bot, Update.model_validate({"update_id": 1, "message": message}))

    def delivered_text(self):
        return "\n".join(getattr(call.args[1], "text", "") or "" for call in self.send.await_args_list)

    async def start_profile(self):
        await self.feed("/start")
        await self.feed(ACTION_PROFILE)
        await self.feed(contact={"phone_number": "+0000000000", "user_id": 42, "first_name": "Synthetic"})

    async def test_start_club_meetup_do_not_touch_private_services(self):
        await self.feed("/start")
        await self.feed(ACTION_CLUB)
        await self.feed(ACTION_MEETUP)
        self.assertEqual(await self.state.get_state(), handlers.Interview.choosing_path.state)
        self.assertEqual(await self.state.get_data(), {})
        self.crm.assert_not_awaited()
        self.gpt.assert_not_awaited()
        self.assertEqual(self.send.await_count, 3)
        keyboard = self.send.await_args_list[0].args[1].reply_markup
        self.assertFalse(any(button.request_contact for row in keyboard.keyboard for button in row))

    async def test_contact_requires_explicit_project_choice(self):
        await self.feed("/start")
        await self.feed(contact={"phone_number": "+0000000000", "user_id": 42, "first_name": "Synthetic"})
        self.crm.assert_not_awaited()
        self.assertEqual(await self.state.get_data(), {})
        await self.feed(ACTION_PROFILE)
        self.assertEqual(await self.state.get_state(), handlers.Interview.waiting_contact.state)
        self.assertIn("не требуется", self.delivered_text())

    async def test_repeated_choice_does_not_lose_active_history_or_duplicate_lead(self):
        await self.start_profile()
        await self.feed("First answer")
        before = await self.state.get_data()
        await self.feed(ACTION_PROFILE)
        after = await self.state.get_data()
        self.assertEqual(after["history"], before["history"])
        self.crm.assert_awaited_once()
        self.assertEqual(self.gpt.await_count, 2)

    async def test_pause_is_not_sent_to_gpt_and_resume_preserves_progress(self):
        await self.start_profile()
        before = await self.state.get_data()
        await self.feed("СТОП")
        self.assertEqual(await self.state.get_state(), handlers.Interview.paused.state)
        self.assertEqual(self.gpt.await_count, 1)
        await self.feed("hello")
        self.assertEqual(self.gpt.await_count, 1)
        await self.feed(ACTION_PROFILE)
        self.assertEqual(await self.state.get_state(), handlers.Interview.interviewing.state)
        self.assertEqual((await self.state.get_data())["history"], before["history"])
        self.crm.assert_awaited_once()

    async def test_cancel_contact_returns_to_public_choices(self):
        await self.feed("/start")
        await self.feed(ACTION_PROFILE)
        await self.feed("/cancel")
        self.assertEqual(await self.state.get_state(), handlers.Interview.choosing_path.state)
        self.crm.assert_not_awaited()

    async def test_faq_and_club_during_profile_do_not_become_interview_answers(self):
        await self.start_profile()
        await self.feed("FAQ")
        await self.feed(ACTION_CLUB)
        self.assertEqual(self.gpt.await_count, 1)
        self.assertEqual((await self.state.get_data())["questions_asked"], 1)

    async def test_question_limit_counts_first_question_and_records_last_answer(self):
        await self.start_profile()
        for i in range(8):
            await self.feed(f"Answer {i}")
        self.assertEqual(self.gpt.await_count, 8)
        self.assertEqual(await self.state.get_state(), handlers.Interview.completed.state)
        history = self.resume.await_args.args[0]
        self.assertEqual(history[0], {"role": "assistant", "content": "Synthetic question"})
        self.assertEqual(history[-1]["content"], "Answer 7")
        self.resume.assert_awaited_once()
        self.assertNotIn("/100", self.delivered_text())
        await self.feed(ACTION_PROFILE)
        self.crm.assert_awaited_once()
        self.resume.assert_awaited_once()

    async def test_completion_marker_is_stripped_and_finalizes_once(self):
        await self.start_profile()
        self.gpt.return_value = "Thanks INTERVIEW_COMPLETE"
        await self.feed("My answer")
        await self.feed("Another answer")
        self.assertNotIn("INTERVIEW_COMPLETE", self.delivered_text())
        self.resume.assert_awaited_once()
        self.save.assert_awaited_once()
        self.notify.assert_awaited_once()

    async def test_first_question_marker_never_leaks(self):
        self.gpt.return_value = "INTERVIEW_COMPLETE"
        await self.start_profile()
        self.assertNotIn("INTERVIEW_COMPLETE", self.delivered_text())
        self.resume.assert_awaited_once()

    async def test_start_after_pause_resets_session(self):
        await self.start_profile()
        await self.feed("пауза")
        await self.feed("/start")
        self.assertEqual(await self.state.get_data(), {})
        self.assertEqual(await self.state.get_state(), handlers.Interview.choosing_path.state)

    async def test_resume_upload_marker_and_limit(self):
        await self.start_profile()
        self.gpt.return_value = "Thanks INTERVIEW_COMPLETE"
        with patch.object(self.bot, "get_file", new=AsyncMock(return_value=SimpleNamespace(file_path="synthetic"))), \
             patch.object(self.bot, "download_file", new=AsyncMock(return_value=b"synthetic")), \
             patch.object(handlers.amo, "upload_resume_file", new_callable=AsyncMock) as upload:
            await self.feed("Отправить резюме")
            await self.feed(document={"file_id": "synthetic", "file_unique_id": "synthetic", "file_name": "test.pdf"})
        upload.assert_awaited_once()
        self.assertNotIn("INTERVIEW_COMPLETE", self.delivered_text())
        self.resume.assert_awaited_once()

    async def test_generation_failure_can_retry_without_repeating_interview(self):
        await self.start_profile()
        self.gpt.return_value = "INTERVIEW_COMPLETE"
        self.resume.side_effect = [RuntimeError("offline failure"), {"ai_summary": "retry"}]
        await self.feed("last answer")
        self.assertEqual(await self.state.get_state(), handlers.Interview.finalizing.state)
        self.assertFalse((await self.state.get_data()).get("finalized"))
        self.save.assert_not_awaited()
        await self.feed("повторить")
        self.assertEqual(await self.state.get_state(), handlers.Interview.completed.state)
        self.assertEqual(self.gpt.await_count, 2)
        self.assertEqual(self.resume.await_count, 2)
        self.save.assert_awaited_once()

    async def test_crm_failure_retries_saved_generation(self):
        await self.start_profile()
        self.gpt.return_value = "INTERVIEW_COMPLETE"
        self.save.side_effect = [RuntimeError("offline failure"), None]
        await self.feed("last answer")
        self.assertNotIn("Профиль сохранён.", self.delivered_text())
        self.notify.assert_not_awaited()
        await self.feed("повторить")
        self.resume.assert_awaited_once()
        self.assertEqual(self.save.await_count, 2)
        self.notify.assert_awaited_once()
        self.assertEqual(await self.state.get_state(), handlers.Interview.completed.state)

    async def test_partial_notification_retry_skips_completed_stages_and_recipient(self):
        await self.start_profile()
        self.gpt.return_value = "INTERVIEW_COMPLETE"
        self.notify.side_effect = [{"delivered": [42], "failed": [43]}, {"delivered": [42, 43], "failed": []}]
        await self.feed("last answer")
        self.assertEqual(await self.state.get_state(), handlers.Interview.finalizing.state)
        await self.feed("повторить")
        self.resume.assert_awaited_once()
        self.save.assert_awaited_once()
        self.assertEqual(self.notify.await_args.kwargs["skip_recipients"], [42])
        self.assertTrue((await self.state.get_data())["finalized"])

    async def test_concurrent_completion_updates_are_serialized(self):
        await self.start_profile()
        self.gpt.return_value = "INTERVIEW_COMPLETE"
        async def slow_generation(*args):
            await asyncio.sleep(0.02)
            return {"ai_summary": "result"}
        self.resume.side_effect = slow_generation
        await asyncio.gather(self.feed("last answer"), self.feed("duplicate answer"))
        self.resume.assert_awaited_once()
        self.save.assert_awaited_once()
        self.notify.assert_awaited_once()

    async def test_pause_failed_finalization_then_resume_keeps_stage_checkpoints(self):
        await self.start_profile()
        self.gpt.return_value = "INTERVIEW_COMPLETE"
        self.save.side_effect = [RuntimeError("offline failure"), None]
        await self.feed("last answer")
        await self.feed("стоп")
        self.assertEqual(await self.state.get_state(), handlers.Interview.paused.state)
        await self.feed("hello")
        self.assertEqual(self.save.await_count, 1)
        await self.feed(ACTION_PROFILE)
        self.resume.assert_awaited_once()
        self.assertEqual(await self.state.get_state(), handlers.Interview.completed.state)

    async def test_failed_upload_never_claims_success_or_calls_gpt(self):
        await self.start_profile()
        with patch.object(self.bot, "get_file", new=AsyncMock(return_value=SimpleNamespace(file_path="synthetic"))), \
             patch.object(self.bot, "download_file", new=AsyncMock(return_value=b"synthetic")), \
             patch.object(handlers.amo, "upload_resume_file", new=AsyncMock(return_value=False)):
            await self.feed(document={"file_id": "synthetic", "file_unique_id": "synthetic", "file_name": "test.pdf"})
        self.assertEqual(self.gpt.await_count, 1)
        self.assertIn("Не удалось сохранить файл", self.delivered_text())
        self.assertNotIn("Резюме получено и сохранено", self.delivered_text())

    async def test_upload_without_lead_never_calls_external_upload(self):
        await self.state.set_state(handlers.Interview.interviewing)
        await self.state.update_data(phone="+0000000000")
        with patch.object(handlers.amo, "upload_resume_file", new_callable=AsyncMock) as upload:
            await self.feed(document={"file_id": "synthetic", "file_unique_id": "synthetic", "file_name": "test.pdf"})
        upload.assert_not_awaited()
        self.gpt.assert_not_awaited()
        self.assertNotIn("Резюме получено и сохранено", self.delivered_text())

    async def test_asgi_secret_to_real_dispatch_rejects_foreign_contact(self):
        import httpx
        from aiogram.client.session.aiohttp import AiohttpSession
        await self.state.set_state(handlers.Interview.waiting_contact)
        payload = {"update_id": 1, "message": {"message_id": 1, "date": 0,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "is_bot": False, "first_name": "Synthetic"},
            "contact": {"phone_number": "+0000000000", "user_id": 99, "first_name": "Other"}}}
        with patch.dict(os.environ, {"TELEGRAM_WEBHOOK_SECRET": "synthetic-secret", "TELEGRAM_BOT_TOKEN": "123456:synthetic-placeholder"}), \
             patch.object(AiohttpSession, "make_request", new_callable=AsyncMock) as send:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://offline") as client:
                response = await client.post("/api/telegram", json=payload, headers={"X-Telegram-Bot-Api-Secret-Token": "synthetic-secret"})
        self.assertEqual(response.status_code, 200)
        send.assert_awaited_once()
        self.crm.assert_not_awaited()
        self.gpt.assert_not_awaited()
        self.assertEqual(await self.state.get_data(), {})


class ReminderAndNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def check_reminder(self, *, sent=0, hours=49, current_state=b"Interview:interviewing"):
        now = 1000000
        data = {"last_activity": now-hours*3600, "reminders_sent": sent}
        session = {"user_id": 42, "last_activity": data["last_activity"], "reminders_sent": sent,
                   "key": "fsm:42:42:state", "data_key": "fsm:42:42:data"}
        async def get(key):
            return current_state if key.endswith(":state") else json.dumps(data)
        lock = MagicMock()
        lock.__aenter__ = AsyncMock()
        lock.__aexit__ = AsyncMock(return_value=False)
        redis = SimpleNamespace(get=AsyncMock(side_effect=get), set=AsyncMock(), aclose=AsyncMock(), lock=MagicMock(return_value=lock))
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(scheduler, "get_all_interview_sessions", new=AsyncMock(return_value=[session])), \
             patch.object(scheduler.aioredis, "from_url", return_value=redis), \
             patch.object(scheduler.time, "time", return_value=now):
            await scheduler.check_inactive_candidates(bot, "redis://offline")
        redis.aclose.assert_awaited_once()
        return bot, redis

    async def test_reminder_only_after_48_hours(self):
        bot, redis = await self.check_reminder(hours=47)
        bot.send_message.assert_not_awaited()
        bot, redis = await self.check_reminder(hours=49)
        bot.send_message.assert_awaited_once()
        self.assertEqual(json.loads(redis.set.await_args.args[1])["reminders_sent"], 1)

    async def test_no_second_reminder(self):
        bot, redis = await self.check_reminder(sent=1, hours=200)
        bot.send_message.assert_not_awaited()
        redis.set.assert_not_awaited()

    async def test_pause_after_scan_prevents_reminder(self):
        bot, redis = await self.check_reminder(current_state=b"Interview:paused")
        bot.send_message.assert_not_awaited()

    async def test_nullable_score_does_not_crash_notification(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        await notifier.notify_ceo_pm(bot, 42, 0, {"lead_id": 123}, {"total_score": None})
        bot.send_message.assert_awaited_once()
        self.assertIn("не выставлен", bot.send_message.await_args.args[1])
        self.assertIn("adminwowericom.kommo.com", bot.send_message.await_args.args[1])

    async def test_scan_reads_default_aiogram_keys_and_skips_nonprivate_or_paused(self):
        values = {
            b"fsm:42:42:state": b"Interview:interviewing",
            "fsm:42:42:data": json.dumps({"last_activity": 1}),
            b"fsm:43:43:state": b"Interview:paused",
            b"fsm:-100:42:state": b"Interview:interviewing",
        }
        async def get(key):
            return values.get(key)
        redis = SimpleNamespace(scan=AsyncMock(return_value=(0, list(k for k in values if isinstance(k, bytes)))),
                                get=AsyncMock(side_effect=get), aclose=AsyncMock())
        with patch.object(scheduler.aioredis, "from_url", return_value=redis):
            sessions = await scheduler.get_all_interview_sessions("redis://offline")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["user_id"], 42)
        self.assertEqual(sessions[0]["data_key"], "fsm:42:42:data")
        redis.aclose.assert_awaited_once()

    async def test_reminder_uses_same_lock_key_as_dispatcher_and_reserves_before_send(self):
        from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
        bot, redis = await self.check_reminder()
        expected = DefaultKeyBuilder().build(StorageKey(bot_id=123456, chat_id=42, user_id=42), "lock")
        self.assertEqual(redis.lock.call_args.kwargs["name"], expected)
        redis.set.assert_awaited_once()
        bot.send_message.assert_awaited_once()

    async def test_scan_closes_connection_on_failure(self):
        redis = SimpleNamespace(scan=AsyncMock(side_effect=RuntimeError("offline")), aclose=AsyncMock())
        with patch.object(scheduler.aioredis, "from_url", return_value=redis):
            self.assertEqual(await scheduler.get_all_interview_sessions("redis://offline"), [])
        redis.aclose.assert_awaited_once()
