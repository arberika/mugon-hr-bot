import os
import unittest
from pathlib import Path
from unittest.mock import patch

from onboarding_copy import (
    CLUB_INFO_TEXT,
    PROFILE_CONSENT_TEXT,
    START_TEXT,
    split_completion_signal,
)
from runtime import webhook_replacement_allowed


class ProgressiveOnboardingTests(unittest.TestCase):
    def test_club_entry_does_not_require_private_data(self):
        text = START_TEXT.lower()
        self.assertIn("номер телефона", text)
        self.assertIn("не нужны", text)
        self.assertIn("показ кода", text)

    def test_project_profile_explains_consent_boundary(self):
        text = PROFILE_CONSENT_TEXT.lower()
        self.assertIn("только для", text)
        self.assertIn("не требуется", text)
        self.assertIn("подтверждение навыков", text)

    def test_club_path_is_distinct_from_project_profile(self):
        self.assertIn("добровольно", CLUB_INFO_TEXT.lower())
        self.assertIn("не влияет на участие", CLUB_INFO_TEXT.lower())

    def test_webhook_replacement_is_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(webhook_replacement_allowed())
        with patch.dict(os.environ, {"ALLOW_WEBHOOK_REPLACEMENT": "false"}, clear=True):
            self.assertFalse(webhook_replacement_allowed())
        with patch.dict(os.environ, {"ALLOW_WEBHOOK_REPLACEMENT": "true"}, clear=True):
            self.assertTrue(webhook_replacement_allowed())

    def test_internal_completion_marker_is_not_participant_facing(self):
        visible, complete = split_completion_signal("Спасибо. INTERVIEW_COMPLETE")
        self.assertTrue(complete)
        self.assertEqual(visible, "Спасибо.")

    def test_polling_cutover_does_not_drop_pending_updates(self):
        source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
        self.assertNotIn("drop_pending_updates=True", source)
        self.assertIn("drop_pending_updates=False", source)


if __name__ == "__main__":
    unittest.main()
