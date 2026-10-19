# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which kind of turn notice a chat error row is, for its card's title (``chat_notices``). Pure Python.

API:
    kind(message) -> one of KINDS
        "signin" (sign-in expired), "credits" (not enough credits), "usage" (usage limit), "rate" (rate limit),
        "model" (model unavailable), "context" (conversation too long), "overloaded" (provider busy),
        "offline" (can't reach the provider) or "unknown".

Display only: the stored row and what the agent sees never change. An error row carrying one of the team
account's own sentences (``team_errors``, its ``i18n`` recipe) is classified by that template; any other row by
the provider's text, with the limit and offline patterns ``first_run.notice`` already uses and the Codex limit
codes of ``provider_errors``. A Codex error ``provider_errors.codex_error_text`` prefixed from its ``codexErrorInfo``
(HTTP 429, an overloaded provider) is classified by that prefix first: the provider's classification, not its wording.
"""

import re

from . import first_run, team_errors
from .provider_errors import CODEX_LIMIT_ERRORS, CODEX_OVERLOADED, CODEX_TOO_MANY_REQUESTS

KINDS = ("signin", "credits", "usage", "rate", "model", "context", "overloaded", "offline", "unknown")

_TEMPLATES = {
    "signin": (team_errors.SIGN_IN_AGAIN, team_errors.SIGN_IN, team_errors.EXPIRED, team_errors.ENDED,
               team_errors.LAPSED, team_errors.REUSED, team_errors.DROPPED),
    "usage": (team_errors.LIMITED, team_errors.LIMITED_LATER),
    "overloaded": (team_errors.GATEWAY_DOWN, team_errors.BUSY, team_errors.UPSTREAM, team_errors.UPSTREAM_ANY),
    "offline": (team_errors.UNREACHABLE,),
}
_BY_TEMPLATE = {template: name for name, templates in _TEMPLATES.items() for template in templates}

_PATTERNS = (
    ("signin", r"sign-?in (?:has )?expired|session (?:has )?expired|token (?:has )?expired|credentials? (?:have |has )?expired"
               r"|not (?:logged|signed) in|please (?:log|sign) ?in|(?:log|sign) ?in again|re-?authenticat"
               r"|authentication(?:_error| failed| error| required)|unauthori[sz]ed|invalid[ _-]?api[ _-]?key"
               r"|invalid x-api-key|invalid_grant|\b401\b|/login\b"),
    ("credits", r"credit balance|insufficient[ _](?:credits?|balance|funds|quota)|out of credits|not enough credits"
                r"|payment required|\b402\b|billing"),
    ("context", r"context (?:length|window)|maximum context|prompt is too long|too many tokens|context_length_exceeded"
                r"|input (?:is )?too long|exceeds the (?:model's )?(?:maximum|context)|conversation is too long"),
    ("model", r"model[^.\n]{0,60}(?:not found|not available|unavailable|not supported|does not exist|doesn't exist"
              r"|is not accessible)|model_not_found|(?:unsupported|unknown|invalid) model"
              r"|(?:no|not have) access to (?:the |this )?model"),
    ("rate", r"rate[ _-]?limit|too many requests|\b429\b|slow down"),
    ("overloaded", r"overloaded|\b529\b|\b503\b|service unavailable|temporarily unavailable|server (?:is )?busy"
                   r"|at capacity|high demand"),
)
_COMPILED = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _PATTERNS)


def kind(message):
    """The notice kind of an error row (a dict with ``text`` and perhaps an ``i18n`` recipe)."""
    recipe = message.get("i18n") if isinstance(message, dict) else None
    if isinstance(recipe, (list, tuple)) and recipe and recipe[0] in _BY_TEMPLATE:
        return _BY_TEMPLATE[recipe[0]]
    text = str((message or {}).get("text") or "")
    if any(code in text for code in CODEX_LIMIT_ERRORS - {"rateLimitExceeded"}):
        return "usage"
    if text.startswith(CODEX_TOO_MANY_REQUESTS):
        return "rate"
    if text.startswith(CODEX_OVERLOADED):
        return "overloaded"
    for name, pattern in _COMPILED:
        if pattern.search(text):
            return name
    found = first_run.notice(text)
    if found:
        return "usage" if found["kind"] == "limit" else "offline"
    return "unknown"
