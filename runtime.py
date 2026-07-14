"""Runtime safety policy for Telegram transport configuration."""

from __future__ import annotations

import os


def webhook_replacement_allowed() -> bool:
    """Return true only after an explicit operator opt-in."""
    return os.environ.get("ALLOW_WEBHOOK_REPLACEMENT", "").strip().lower() == "true"
