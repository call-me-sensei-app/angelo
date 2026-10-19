# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which notice card a chat error row gets (notice_kinds): known cases get their own title, never the generic one.

Dependency-free: the classifier only; the cards are captured by test_blender_chat_replies.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import notice_kinds, team_errors  # noqa: E402
from angelo_agent.provider_errors import codex_error_text  # noqa: E402
from angelo_agent.ui_text import notice  # noqa: E402

CASES = {
    "signin": ['API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}',
               "Invalid API key · Please run /login", "Not logged in. Please sign in again.",
               "Your session has expired; re-authenticate to continue."],
    "credits": ["Credit balance is too low", "Your credit balance is too low to access the Anthropic API.",
                "insufficient_quota: You exceeded your current quota, please check your plan and billing details.",
                "Payment required (402)"],
    "usage": ["You've hit your session limit · resets 4:30pm (UTC)", "Claude AI usage limit reached|1760040000",
              codex_error_text({"message": "You've reached your weekly limit.", "codexErrorInfo": "usageLimitExceeded"})],
    "rate": ["API Error: 429 Too Many Requests", "rate_limit_error: Number of requests has exceeded your rate limit",
             codex_error_text({"message": "Slow down.", "codexErrorInfo": "rateLimitExceeded"})],
    "model": ["model: claude-old-1 not found", "The model `gpt-9` does not exist or you do not have access to it.",
              "model_not_found", "Unsupported model for this account"],
    "context": ["prompt is too long: 230000 tokens > 200000 maximum", "context_length_exceeded",
                "This model's maximum context length is 128000 tokens."],
    "overloaded": ['API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}',
                   "503 Service Unavailable", "The server is busy; we are experiencing high demand."],
    "offline": ["API Error: Connection error.", "Request timed out.", "getaddrinfo ENOTFOUND api.anthropic.com"],
    "unknown": ["IndexError: list index out of range (line 7)", "Something odd happened.", ""],
}


class NoticeKindTests(unittest.TestCase):
    def test_provider_texts(self):
        for expected, texts in CASES.items():
            for text in texts:
                with self.subTest(text=text):
                    self.assertEqual(notice_kinds.kind({"role": "error", "text": text}), expected)

    def test_team_sentences_are_classified_by_their_template(self):
        for template, expected in ((team_errors.EXPIRED, "signin"), (team_errors.ENDED, "signin"),
                                   (team_errors.GATEWAY_DOWN, "overloaded"), (team_errors.LIMITED_LATER, "usage")):
            with self.subTest(template=template):
                self.assertEqual(notice_kinds.kind(dict(notice(template), role="error")), expected)
        row = dict(notice(team_errors.LIMITED, seconds=30), role="error")
        self.assertEqual(notice_kinds.kind(row), "usage")
        self.assertEqual(notice_kinds.kind(dict(notice(team_errors.NO_SEAT), role="error")), "unknown")

    def test_every_kind_is_declared(self):
        self.assertEqual(set(CASES), set(notice_kinds.KINDS))

    def test_the_card_has_a_title_for_every_known_kind(self):
        source = (Path(notice_kinds.__file__).parent / "chat_notices.py").read_text()
        for name in notice_kinds.KINDS:
            if name not in {"usage", "unknown"}:
                self.assertIn('"%s": (' % name, source)


if __name__ == "__main__":
    unittest.main()
