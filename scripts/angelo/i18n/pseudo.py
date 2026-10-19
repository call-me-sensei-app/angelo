# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A pseudo-locale for QA: every translated string is visibly marked, accented and about 40% longer.

``⟦Ȧççèṗţ⟧`` on screen means "this came through the catalogue"; plain English
means it bypassed translation; clipped or overlapping text means a real
translation of that length would overflow. It is never shipped: the build tools
write it into a scratch locale folder, and the host test also uses it to prove
that no translated byte reaches a model.
"""

import re

from i18n import po
from i18n.validate import PLACEHOLDER

OPEN, CLOSE = "⟦", "⟧"
ACCENTS = dict(zip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                   "àƀçðéƒĝħîĵķłɱñöþɋŕšţûṽŵẋýžÀɃÇÐÉƑĜĤÎĴĶŁḾÑÖÞɊŔŠŢÛṼŴẊÝŽ"))
PAD = "~"


def pseudo(text, expansion=0.40):
    """Accent letters, keep placeholders and shortcuts untouched, pad, and wrap in ⟦ ⟧."""
    pieces, last = [], 0
    for match in PLACEHOLDER.finditer(text):
        pieces.append(("text", text[last:match.start()]))
        pieces.append(("keep", match.group(0)))
        last = match.end()
    pieces.append(("text", text[last:]))
    body = "".join(piece if kind == "keep" else "".join(ACCENTS.get(ch, ch) for ch in piece) for kind, piece in pieces)
    padding = PAD * max(1, round(len(re.sub(r"\{[^}]*\}", "", text)) * expansion)) if text.strip() else ""
    if "\n" in body:  # Keep line structure: pad only the last line.
        return OPEN + body + padding + CLOSE
    return OPEN + body + padding + CLOSE


def build(template_entries, language="xx"):
    """A catalogue (header, entries) translating every template entry into the pseudo-locale."""
    header = "\n".join(["Project-Id-Version: Angelo 1.0 pseudo-locale", f"Language: {language}", "MIME-Version: 1.0",
                        "Content-Type: text/plain; charset=UTF-8", "Content-Transfer-Encoding: 8bit"])
    return header, [po.Entry(entry.id, pseudo(entry.id), entry.ctxt) for entry in template_entries]
