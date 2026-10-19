"""Offline tests of exact production function bodies, with mocked boundaries.

Uses AST extraction solely because optional runtime packages are not installed.
This does not test aiogram routing, Redis, Telegram, or production integration.
Set MUGON_SOURCE_DIR to run against an alternate source tree.
"""
import ast
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock
from onboarding_copy import split_completion_signal

ROOT = Path(os.environ.get("MUGON_SOURCE_DIR", Path(__file__).resolve().parents[1]))


def load_functions(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    if {node.name for node in selected} != set(names):
        raise AssertionError("Expected production functions missing")
    # Postpone annotations; all executable function statements remain unchanged.
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future] + selected, type_ignores=[]))
    exec(compile(module, str(ROOT / filename), "exec"), namespace)
    return namespace


class ContactOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = NS(
            contact=NS(user_id=42, phone_number="+0000000000"),
            from_user=NS(id=42, username="synthetic", first_name="Test", last_name="User"),
            answer=AsyncMock(),
        )
        self.state = NS(update_data=AsyncMock(), set_state=AsyncMock())
        self.amo = NS(find_or_create_lead=AsyncMock(return_value=123))
        self.gpt = AsyncMock(return_value="Synthetic first question")
        self.ns = load_functions("handlers.py", ["got_contact"], {
            "amo": self.amo, "ask_hr_gpt": self.gpt,
            "split_completion_signal": split_completion_signal,
            "share_contact_kb": lambda: "share-own-contact",
            "ReplyKeyboardRemove": lambda: "remove-keyboard",
            "Interview": NS(phone_verified="verified", interviewing="interview"),
            "PIPELINE_ID": 14089467, "STATUS_NEW": 108772599,
            "time": NS(time=lambda: 123456.0),
        })

    async def call(self):
        await self.ns["got_contact"](self.message, self.state, NS())

    async def assert_rejected(self):
        await self.call()
        self.amo.find_or_create_lead.assert_not_awaited()
        self.gpt.assert_not_awaited()
        self.state.update_data.assert_not_awaited()
        self.state.set_state.assert_not_awaited()
        self.message.answer.assert_awaited_once()
        self.assertEqual(self.message.answer.call_args.kwargs["reply_markup"], "share-own-contact")

    async def test_own_contact_continues_existing_flow(self):
        await self.call()
        self.amo.find_or_create_lead.assert_awaited_once()
        self.assertEqual(self.amo.find_or_create_lead.call_args.kwargs["tg_id"], "42")
        self.assertEqual(self.amo.find_or_create_lead.call_args.kwargs["pipeline_id"], 14089467)
        self.assertEqual(self.state.set_state.await_args_list[-1].args, ("interview",))
        self.gpt.assert_awaited_once()

    async def test_other_person_contact_is_rejected(self):
        self.message.contact.user_id = 43
        await self.assert_rejected()

    async def test_contact_without_owner_id_is_rejected(self):
        self.message.contact.user_id = None
        await self.assert_rejected()

    async def test_missing_contact_is_rejected(self):
        self.message.contact = None
        await self.assert_rejected()

    async def test_missing_sender_is_rejected(self):
        self.message.from_user = None
        await self.assert_rejected()


class PollingStartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = NS(
            get_webhook_info=AsyncMock(return_value=NS(url="")),
            delete_webhook=AsyncMock(),
            session=NS(close=AsyncMock()),
        )
        self.dp = NS(include_router=Mock(), message=NS(middleware=Mock()), start_polling=AsyncMock())
        self.tasks = []
        def create_task(coro):
            task = asyncio.create_task(coro)
            self.tasks.append(task)
            return task
        self.spawn = Mock(side_effect=create_task)
        self.scheduler = AsyncMock()
        self.storage = NS(close=AsyncMock(), create_isolation=Mock())
        self.ns = load_functions("bot.py", ["ensure_polling_allowed", "main"], {
            "Bot": Mock(return_value=self.bot), "Dispatcher": Mock(return_value=self.dp),
            "RedisStorage": NS(from_url=Mock(return_value=self.storage)),
            "os": NS(environ={"TELEGRAM_BOT_TOKEN": "synthetic-not-a-credential"}),
            "router": NS(), "ThrottlingMiddleware": Mock(), "VerificationMiddleware": Mock(),
            "logger": Mock(), "asyncio": NS(create_task=self.spawn, gather=asyncio.gather), "run_scheduler": self.scheduler,
        })

    async def asyncTearDown(self):
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def test_active_webhook_blocks_polling_and_scheduler(self):
        self.bot.get_webhook_info.return_value = NS(url="https://example.invalid/webhook")
        with self.assertRaisesRegex(RuntimeError, "active Telegram webhook"):
            await self.ns["main"]()
        self.dp.start_polling.assert_not_awaited()
        self.spawn.assert_not_called()
        self.bot.delete_webhook.assert_not_awaited()
        self.bot.session.close.assert_awaited_once()
        self.storage.close.assert_awaited_once()

    async def test_lookup_failure_fails_closed_and_closes_session(self):
        self.bot.get_webhook_info.side_effect = TimeoutError("synthetic timeout")
        with self.assertRaises(TimeoutError):
            await self.ns["main"]()
        self.dp.start_polling.assert_not_awaited()
        self.spawn.assert_not_called()
        self.bot.delete_webhook.assert_not_awaited()
        self.bot.session.close.assert_awaited_once()
        self.storage.close.assert_awaited_once()

    async def test_empty_webhook_allows_polling_without_deletion(self):
        await self.ns["main"]()
        self.bot.get_webhook_info.assert_awaited_once()
        self.dp.start_polling.assert_awaited_once_with(self.bot, allowed_updates=["message", "callback_query"])
        self.spawn.assert_called_once()
        self.bot.delete_webhook.assert_not_awaited()
        self.bot.session.close.assert_awaited_once()
        self.storage.close.assert_awaited_once()

    async def test_polling_failure_still_closes_session(self):
        self.dp.start_polling.side_effect = RuntimeError("synthetic polling error")
        with self.assertRaisesRegex(RuntimeError, "synthetic polling error"):
            await self.ns["main"]()
        self.bot.delete_webhook.assert_not_awaited()
        self.bot.session.close.assert_awaited_once()
        self.storage.close.assert_awaited_once()

    def test_entrypoint_contains_no_webhook_mutation_calls(self):
        tree = ast.parse((ROOT / "bot.py").read_text())
        calls = [node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        self.assertNotIn("delete_webhook", calls)
        self.assertNotIn("set_webhook", calls)


if __name__ == "__main__":
    unittest.main()
