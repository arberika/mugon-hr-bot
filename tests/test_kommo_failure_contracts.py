"""Synthetic HTTP responses verify Kommo success/failure contracts."""
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from amocrm import AmoCRM
import notifier


def response(status=200, payload=None):
    result = MagicMock()
    result.status = status
    result.json = AsyncMock(return_value=payload or {})
    result.text = AsyncMock(return_value="synthetic failure")
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=False)
    return result


class KommoFailureTests(unittest.IsolatedAsyncioTestCase):
    async def upload(self, responses):
        client = AmoCRM("adminwowericom.kommo.com", long_term_token="synthetic")
        session = MagicMock()
        session.post.side_effect = responses
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("amocrm.aiohttp.ClientSession", return_value=session):
            return await client.upload_resume_file(123, io.BytesIO(b"test"), "test.pdf")

    async def test_upload_http_failure_is_false(self):
        self.assertFalse(await self.upload([response(500)]))

    async def test_missing_uuid_is_false(self):
        self.assertFalse(await self.upload([response(201, {"other": "data"})]))

    async def test_attachment_http_failure_is_false(self):
        self.assertFalse(await self.upload([response(201, {"uuid": "synthetic"}), response(500)]))

    async def test_confirmed_attachment_is_true(self):
        self.assertTrue(await self.upload([response(201, {"uuid": "synthetic"}), response(201)]))

    async def test_profile_note_failure_raises(self):
        client = AmoCRM("adminwowericom.kommo.com", long_term_token="synthetic")
        session = MagicMock()
        session.post.return_value = response(500)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("amocrm.aiohttp.ClientSession", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "not saved"):
                await client.update_lead_fields(123, {"total_score": None})

    async def test_notification_retry_only_sends_to_failed_recipient(self):
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=[None, RuntimeError("offline")])
        result = await notifier.notify_ceo_pm(bot, 42, 43, {}, {"total_score": None})
        self.assertEqual(result, {"delivered": [42], "failed": [43]})
        bot.send_message = AsyncMock()
        result = await notifier.notify_ceo_pm(bot, 42, 43, {}, {}, skip_recipients=result["delivered"])
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.await_args.args[0], 43)
        self.assertEqual(result, {"delivered": [42, 43], "failed": []})

    async def test_same_notification_recipient_is_not_duplicated(self):
        bot = MagicMock()
        bot.send_message = AsyncMock()
        await notifier.notify_ceo_pm(bot, 42, 42, {}, {})
        bot.send_message.assert_awaited_once()
