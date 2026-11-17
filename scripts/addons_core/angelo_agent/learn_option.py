# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which chats have "Teach me as you work" on (learn mode): the person's choice, kept per chat. No Blender here.

With it on, the how-to cards of a chat's latest request are open by themselves (``chat_rows``); with it off they
open when a step is opened. It changes nothing a model reads and nothing on the request path.

Kept in one small file of the state folder, ``learn.json``: ``{"version": 1, "teach": [chat id, ...]}``, newest last,
at most ``MAX_CHATS`` (the oldest go first). Not in a chat's options: those travel to workers and into cache keys
(``chat_settings.stored_options``), and this is a view setting. A build that does not know the file ignores it.

``load`` reads the file once (at register); ``teaching`` answers from memory, so the transcript's draw does no disk
work. ``set_teaching`` writes the whole file through a temporary file and ``shared_replace`` (the add-on's one
replace: on Windows it lands while another Angelo on the profile reads the file); a failed or refused write raises
``OSError`` and leaves memory as the file is. Reads go through ``shared_replace`` too, so ours never refuse a
writer. Main thread only.
"""

import json
import os
from pathlib import Path

from . import owned_paths, shared_replace

NAME = "learn.json"
VERSION = 1
MAX_CHATS = 512
MAX_ID = 128
_teach = {}   # State folder (str) -> the chat ids with it on, oldest first.
_current = ""   # The state folder the running app uses (``use``): what ``teaching_now`` answers for.


def _clean(found):
    ids = [item for item in found if isinstance(item, str) and 0 < len(item) <= MAX_ID] if isinstance(found, list) else []
    return list(dict.fromkeys(ids))[-MAX_CHATS:]


def load(directory):
    """Read the choices of ``directory`` (the state folder). A missing or unreadable file is "off everywhere"."""
    try:
        record = json.loads(shared_replace.read_text(Path(directory) / NAME))
    except (OSError, ValueError):
        record = {}
    _teach[str(directory)] = _clean(record.get("teach") if isinstance(record, dict) else None)


def use(directory):
    """Read ``directory`` and make it the folder ``teaching_now`` answers for (``learn_ui.register``)."""
    global _current
    load(directory)
    _current = str(directory)


def teaching_now(chat_id):
    """Whether ``chat_id`` has it on in the running app's state folder. Memory only and no Blender, so the
    transcript's draw can ask it (``learn_rows.begin``): False before ``use``."""
    return bool(_current) and chat_id in _teach.get(_current, ())


def teaching(directory, chat_id):
    """Whether ``chat_id`` has Teach me as you work on. From memory: False until ``load`` has read the folder."""
    return chat_id in _teach.get(str(directory), ())


def set_teaching(directory, chat_id, on):
    """Turn it on or off for ``chat_id`` and save. Raises ``OSError`` when the file cannot be written."""
    owned_paths.require_writable(directory)
    if not isinstance(chat_id, str) or not 0 < len(chat_id) <= MAX_ID:
        raise ValueError("A chat id is a short string")
    if str(directory) not in _teach:
        load(directory)
    ids = [item for item in _teach[str(directory)] if item != chat_id]
    if on:
        ids.append(chat_id)
    ids = ids[-MAX_CHATS:]
    path = Path(directory) / NAME
    temporary = path.with_name("%s.%d.tmp" % (NAME, os.getpid()))
    try:
        temporary.write_text(json.dumps({"version": VERSION, "teach": ids}), encoding="utf-8")
        shared_replace.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    _teach[str(directory)] = ids


def forget():
    """Drop what was read (unregister)."""
    global _current
    _teach.clear()
    _current = ""
