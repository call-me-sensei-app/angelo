# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Why a conversation's reference can't be read now but may be later (``references``, which names them too). Both are
ValueErrors, so a caller that only knows a reference failed handles them as it always did."""


class ReferencePending(ValueError):
    """A PDF's text is being extracted on another thread; read it again shortly."""


class ImageMissing(ValueError):
    """No image file with that reference_id in this conversation's folder, yet: unlike a malformed id or a file over
    the limit, one may still be written (a live page waits for it, ``session_page_pending``)."""
