import asyncio
from pathlib import Path
import unittest

from amocrm import AmoCRM


ROOT = Path(__file__).resolve().parents[1]


class KommoCutoverTests(unittest.TestCase):
    def test_long_term_token_does_not_require_oauth_refresh(self):
        client = AmoCRM(
            domain="https://adminwowericom.kommo.com/",
            long_term_token="local-test-token",
        )
        self.assertEqual(client.base_url, "https://adminwowericom.kommo.com/api/v4")
        self.assertEqual(asyncio.run(client._get_token()), "local-test-token")

    def test_active_runtime_has_no_legacy_account_markers(self):
        active_files = [
            ".env.example",
            "amocrm.py",
            "handlers.py",
            "gpt.py",
            "notifier.py",
            "oauth_setup.py",
            "app.py",
        ]
        for relative_path in active_files:
            source = (ROOT / relative_path).read_text()
            for forbidden in ("eriarwork2201", ".amocrm.ru", "10599910", "83583878"):
                self.assertNotIn(forbidden, source, f"{forbidden} found in {relative_path}")

    def test_current_pipeline_defaults_are_declared(self):
        source = (ROOT / "handlers.py").read_text()
        self.assertIn('"14089467"', source)
        self.assertIn('"108772599"', source)

    def test_vercel_webhook_has_health_and_secret_guard(self):
        source = (ROOT / "app.py").read_text()
        self.assertIn('@app.get("/health")', source)
        self.assertIn('@app.post("/api/telegram")', source)
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", source)


if __name__ == "__main__":
    unittest.main()
