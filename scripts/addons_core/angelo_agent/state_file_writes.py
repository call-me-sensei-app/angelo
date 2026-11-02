# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bytes and the atomic replace of two state files with background writers: performance and usage.

Their stores (``telemetry.TelemetryStore``, ``usage_meter.UsageLedger``) call these inside their write turn
(``write_turn.WriteTurn``); nothing here takes a lock or keeps state.
"""

import json
import os
import uuid


def write_receipts(path, records, limit):
    """``performance.json``: the newest receipts that fit in ``limit`` bytes, fsynced, then replaced at once.

    True when written; on any failure the temporary file is removed and the old file stays."""
    temporary = path.with_name(".performance-" + str(uuid.uuid4()) + ".tmp")
    try:
        encoded = json.dumps({"version": 1, "requests": records}, ensure_ascii=False, allow_nan=False)
        while len(encoded.encode("utf-8")) > limit and records:
            # The budget is fixed; receipts are not all one size. The file keeps the newest that fit.
            records = records[max(1, len(records) // 8):]
            encoded = json.dumps({"version": 1, "requests": records}, ensure_ascii=False, allow_nan=False)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True
    except (OSError, ValueError, TypeError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def encode_usage(chats, limit):
    """``usage.json``'s bytes: the newest chats that fit in ``limit`` bytes (UTF-8), the oldest left out first."""
    head, tail = b'{"version":1,"chats":{', b"}}"
    size, kept = len(head) + len(tail), []
    for chat_id in sorted(chats, key=lambda key: chats[key]["updated"], reverse=True):
        part = (json.dumps(chat_id, ensure_ascii=False) + ":" + json.dumps(
            chats[chat_id], ensure_ascii=False, allow_nan=False, separators=(",", ":"))).encode("utf-8")
        if size + len(part) + bool(kept) > limit:
            break
        size += len(part) + bool(kept)
        kept.append(part)
    return head + b",".join(kept) + tail
