#!/usr/bin/env python3
"""Fail when tracked text files contain high-confidence credential patterns."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "Telegram bot token": re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{35}\b"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def main() -> int:
    findings: list[str] = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = path.relative_to(ROOT)
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative}: possible {label}")

    if findings:
        print("Secret scan failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("Secret scan passed: no high-confidence credentials in tracked files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
