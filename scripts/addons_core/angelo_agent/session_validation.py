# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Readable fail-closed recovery of malformed transcript elements/identities."""

import json
import math
import uuid

from .provider_registry import CODING_MODES
from .ui_text import model_notice, n_, stored_recipe

ROLES = frozenset({"user", "assistant", "system", "error", "activity", "thinking"})


def identity(value):
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except (ValueError, AttributeError):
        return None


def public_string(value, maximum):
    return (type(value) is str and len(value) <= maximum
            and not any(0xD800 <= ord(character) <= 0xDFFF for character in value))


def timestamp(value):
    return type(value) in {int, float} and 0 <= value <= 2**53 and math.isfinite(value)


def _with_recipe(message):
    """A system or error row keeps its ``i18n`` display recipe only while it is valid and matches the text; others never have one."""
    recipe = stored_recipe(message) if message["role"] in ("system", "error") else None
    message.pop("i18n", None)
    if recipe is not None:
        message["i18n"] = recipe
    return message


def messages(value):
    """Keep whole valid rows; report any discarded/corrupt row to close mutations.

    Content is historical/untrusted, not approval. Do not truncate message text
    or tool results into apparently complete evidence during recovery.
    """
    if type(value) is not list:
        return [], False
    output, verified = [], True
    for message in value:
        if type(message) is not dict or type(message.get("role")) is not str or message["role"] not in ROLES:
            verified = False
            continue
        role = message["role"]
        text = message.get("text", "")
        valid = public_string(text, 1024 * 1024)
        if role == "activity":
            valid &= (public_string(message.get("call_id"), 4096)
                      and public_string(message.get("tool", ""), 96)
                      and type(message.get("arguments", {})) is dict
                      and type(message.get("result", {})) is dict
                      and public_string(message.get("status", "queued"), 64))
        if role == "thinking":
            valid &= (public_string(message.get("status", "completed"), 64)
                      and timestamp(message.get("duration_ms", 0)))
        if "model_text" in message:   # A model_notice's words for the model, on a system or error row only.
            valid &= role in ("system", "error") and public_string(message["model_text"], 1024 * 1024)
        refs = message.get("references", [])
        if type(refs) is not list or len(refs) > 128:
            valid = False
        elif any(type(ref) is not dict or not public_string(ref.get("path"), 16384)
                 or not public_string(ref.get("name"), 1024) or not public_string(ref.get("kind"), 64) for ref in refs):
            valid = False
        try:
            encoded = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
            valid &= len(encoded) <= 2 * 1024 * 1024
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            valid = False
        if valid:
            output.append(_with_recipe(dict(message, text=text)))
        else:
            verified = False
    return output, verified


def header(raw, state, *, session_id=None):
    """Normalize safe scalar fields and return whether their claims verified."""
    verified = True
    supplied = identity(raw.get("id"))
    if supplied is None or session_id is not None and supplied != session_id:
        verified = False
    elif session_id is None:
        state["id"] = supplied
    provider = raw.get("provider", state["provider"])
    if type(provider) is not str or provider not in CODING_MODES:
        verified = False
    else:
        state["provider"] = provider
    for key in ("native_id", "model"):
        value = raw.get(key, state[key])
        if key == "native_id" and value is None or public_string(value, 512):
            state[key] = value
        else:
            verified = False
    connection = raw.get("connection_id")
    if connection is not None and identity(connection) is None:
        verified = False
    else:
        state["connection_id"] = identity(connection)
    updated = raw.get("updated", state["updated"])
    if timestamp(updated):
        state["updated"] = updated
    else:
        verified = False
    version = raw.get("toolset_version", 1)
    if type(version) is int and 1 <= version <= 65536:
        state["toolset_version"] = version
    else:
        state["toolset_version"] = 1
        verified = False
    return verified


def index_record(record):
    if type(record) is not dict or identity(record.get("id")) is None:
        return None
    title, provider, updated = record.get("title"), record.get("provider"), record.get("updated")
    return {"id": identity(record["id"]), "title": title if public_string(title, 64) else "Recovered chat",
            "provider": provider if type(provider) is str and provider in CODING_MODES else "codex_subscription",
            "updated": updated if timestamp(updated) else 0}


def _uncertain(message):
    return (message.get("role") == "system" and type(message.get("restore_state")) is str
            and message["restore_state"] in {"pending", "partial"} and identity(message.get("restore_checkpoint")) is not None)


def uncertain_recovery(value):
    """A durable pre-open notice is not proof of the scene that won the load."""
    return any(_uncertain(message) for message in value)


# After a restore that did not finish (``uncertain_recovery``) a load starts a fresh native context and adds one row:
# the model reads the first text, the person the second (``ui_text.model_notice``), never the model's instruction.
RECOVERY_MODEL_TEXT = ("A snapshot recovery has an incomplete stored outcome. Its safety link is preserved. Inspect the "  # i18n: the model reads it
                       "current scene/files; a fresh native context and observations are required before continuing. "
                       "Do not automatically repeat recovery or earlier edits.")
RECOVERY_CHECK = n_("A restore in this chat didn't fully finish. Check the scene and your files before you continue. "
                    "Undo restore brings back the scene from before.")


def rewords(message):
    """Whether ``recovery_check`` changes ``message`` in place: an earlier build's row of the model's words alone."""
    return (message.get("role") == "system" and message.get("text") == RECOVERY_MODEL_TEXT
            and "model_text" not in message)


def recovery_check(rows):
    """``[row]`` to add after an unfinished restore, or ``[]`` when a row since that restore already says it.

    A row stored by an earlier build as the model's instruction alone becomes this row in place, so the person's
    chat never shows the model's words.
    """
    last = None
    for index, message in enumerate(rows):
        if _uncertain(message):
            last = index
        elif rewords(message):
            message.pop("i18n", None)
            message.update(recovery_check=True, **model_notice(RECOVERY_MODEL_TEXT, RECOVERY_CHECK))
    if last is None or any(message.get("recovery_check") is True for message in rows[last + 1:]):
        return []
    # No restore_checkpoint of its own: that restore's notice keeps its Undo restore and its recorded images.
    return [{"role": "system", "recovery_check": True, **model_notice(RECOVERY_MODEL_TEXT, RECOVERY_CHECK)}]
