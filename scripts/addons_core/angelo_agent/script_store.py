# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripts kept by id in the conversation: fetched again, and run again unchanged or patched. No Blender.

Recorded runs (20 remarks in 9 trials, mostly GPT) lost a script's text between calls: a Codex
code-mode cell never kept it, a later turn's context does not carry tool inputs (``routing.handoff`` hands off
results, not arguments), and a raised script's one-line fix meant sending the whole script again. A patch saves an
estimated median 27.6 s per re-send (Opus one-shots).

* Every finished script's result names it (``script_after.notes``): ``script`` is ``s`` and the first eight hex
  digits of the SHA-256 of its code (``script_id``). The id is the text's own digest, so it needs no counter and
  no new saved state, and the same text always has the same id.
* The text is this conversation's own transcript (``activity_rows``): every script call's row keeps the arguments
  it was admitted with, redacted of the connection's key, and is saved with the chat. ``find`` reads those rows
  only (never another conversation's, never a collaborator's); nothing new is stored or sent anywhere.
* ``angelo_script`` (read-only) returns a script's description and code.
* ``angelo_execute_python`` takes ``base`` (an id) and ``edits`` (``[{find, replace}]``) instead of ``code``.
  Each ``find`` must occur exactly once in the text as the edits before it left it; they apply in order, and no
  edits runs the base unchanged. ``expand`` builds the full code in ``Controller.resolve_call``, before
  validation, routing, the invocation digest and the approval card, so the person reviews, and every consent
  covers, exactly the text that runs, as for any script.

Refused before anything is shown or run (``ScriptStoreError``, a VALIDATION refusal): ``base`` together with
``code``, ``edits`` without ``base``, an id no row of this conversation has (a row whose text lost the key to
redaction no longer matches its id, so a script that held the key is never a base), an id two different texts
share, and an edit whose ``find`` occurs other than once. The built code then meets every rule of a script sent
in full (length, NUL, syntax).
"""

import hashlib
import re

from .refusal_codes import RefusalError

TOOL_NAME = "angelo_script"
SCRIPT_TOOL = "angelo_execute_python"
SCRIPT_ID = r"^s[0-9a-f]{8}$"
MAX_EDITS = 32
MAX_CODE = 32768  # A script's own limit (tool_specs.validate_call).
MAX_BUILT = 64  # Patch calls remembered for a re-delivery (``_built``).
# Lengths are the built script's (32,768 characters, tool_specs.validate_call), not each edit's.
EDIT = {"type": "object", "properties": {"find": {"type": "string", "minLength": 1}, "replace": {"type": "string"}},
        "required": ["find", "replace"], "additionalProperties": False}
# The fields angelo_execute_python gains (tool_specs): the patch form beside ``code``.
PATCH_FIELDS = {"base": {"type": "string", "pattern": SCRIPT_ID},
                "edits": {"type": "array", "items": EDIT, "maxItems": MAX_EDITS}}
PATCH_SENTENCE = (" A result names its script; to rerun one, unchanged or patched, send base: that id and edits "
                  "[{find, replace}] (in order, each find exactly once) instead of code. angelo_script returns its code.")
SCRIPT_TOOLS = [{
    "name": TOOL_NAME,
    "description": ("Read-only: a script this conversation sent, by its result's script id: its description and "
                    "code, as the transcript keeps it (the connection's key redacted)."),
    "inputSchema": {"type": "object", "properties": {"script": {"type": "string", "pattern": SCRIPT_ID}},
                    "required": ["script"], "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}]


class ScriptStoreError(RefusalError):
    """A patch or fetch refused before anything is shown or run; nothing changed."""

    def __init__(self, message):
        super().__init__(message, code="VALIDATION")


def script_id(code):
    """``s`` and the first eight hex digits of the SHA-256 of ``code`` (UTF-8)."""
    return "s" + hashlib.sha256(code.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def _rows(controller):
    """``(description, code)`` of this conversation's scripts, newest first, under the chat's lock.

    Its transcript rows, then its calls still pending: a call's row is added when the main thread pumps its
    admission event, which can come after a fast next call is admitted. A collaborator's call is never one. A
    pending call's text is redacted as its row will be (``activity_rows.redacted``), so a script that held the
    connection's key is never a base, wherever it is read from.
    """
    from .activity_rows import redacted
    with controller.lock:
        rows = [row.get("arguments") for row in controller.state.get("messages", ()) if isinstance(row, dict)
                and row.get("role") == "activity" and row.get("tool") == SCRIPT_TOOL and not row.get("collaborator")]
        calls = [call.arguments for call in list(getattr(controller, "pending", {}).values())
                 if getattr(call, "name", None) == SCRIPT_TOOL and getattr(call, "parent_call", None) is None]
    if getattr(controller, "api_key", None):
        calls = [redacted(controller, arguments) for arguments in calls]
    found = []
    for arguments in list(reversed(rows)) + calls:
        if isinstance(arguments, dict) and isinstance(arguments.get("code"), str):
            found.append((str(arguments.get("description", "")), arguments["code"]))
    return found


def find(controller, identity):
    """``(description, code)`` of the script ``identity`` names in this conversation, else ``ScriptStoreError``."""
    if not isinstance(identity, str) or not re.fullmatch(SCRIPT_ID, identity):
        raise ScriptStoreError("A script id is s and eight hex digits, as a script's result names it (script)")
    matches = [(description, code) for description, code in _rows(controller) if script_id(code) == identity]
    if not matches:
        raise ScriptStoreError("%s is not a script this conversation sent (or its text held the connection's key, "
                               "which the transcript redacts); send the script's code" % identity)
    if len({code for _, code in matches}) > 1:
        raise ScriptStoreError("%s names two different scripts in this conversation; send the code" % identity)
    return matches[0]


def occurrences(text, needle):
    """How many times ``needle`` starts in ``text``, overlapping ones included, counting no further than 2.

    ``str.count`` counts only matches that do not overlap, so "))" in "f(g(h()))" read as once and the patch
    went to the first of two places.
    """
    count, start = 0, text.find(needle)
    while start != -1 and count < 2:
        count += 1
        start = text.find(needle, start + 1)
    return count


def patched(code, edits):
    """``code`` with ``edits`` applied in order, each ``find`` occurring exactly once where it applies."""
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict) or set(edit) != {"find", "replace"} or not all(
                isinstance(edit[key], str) for key in edit) or not edit["find"]:
            raise ScriptStoreError("edits[%d] needs exactly find (non-empty text) and replace (text)" % index)
        count = occurrences(code, edit["find"])
        if count != 1:
            raise ScriptStoreError("edits[%d].find occurs %d times in the script as the edits before it left it; it "
                                   "must occur exactly once (add surrounding lines to make it unique)" % (index, count))
        code = code.replace(edit["find"], edit["replace"], 1)
        if len(code) > MAX_CODE:  # Never build more than a script may hold (tool_specs.validate_call).
            raise ScriptStoreError("edits[%d] makes the script longer than %s characters" % (index, format(MAX_CODE, ",")))
    return code


def expand(controller, name, arguments):
    """A script call's arguments with ``base`` and ``edits`` built into ``code``; any other call unchanged."""
    if name != SCRIPT_TOOL or not isinstance(arguments, dict) or not ({"base", "edits"} & set(arguments)):
        return arguments
    if "code" in arguments:
        raise ScriptStoreError("Send either code or base (with edits), not both")
    if "base" not in arguments:
        raise ScriptStoreError("edits apply to a base: the id a script's result named (script)")
    edits = arguments.get("edits", [])
    if not isinstance(edits, list) or len(edits) > MAX_EDITS:
        raise ScriptStoreError("edits is a list of at most %d {find, replace}" % MAX_EDITS)
    key = _submitted(arguments)
    built = _built(controller)
    try:
        _, code = find(controller, arguments["base"])
    except ScriptStoreError:
        if key in built:  # The same call sent again: what its first admission built, so its replay is found.
            return dict(built[key])
        raise
    expanded = {name: value for name, value in arguments.items() if name not in ("base", "edits")}
    expanded["code"] = patched(code, edits)
    built[key] = dict(expanded)
    while len(built) > MAX_BUILT:
        built.pop(next(iter(built)))
    return expanded


def _submitted(arguments):
    """A digest of a patch call's arguments exactly as sent (base and edits, not the code they build)."""
    import json
    text = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _built(controller):
    """This conversation's recent patch calls -> the arguments their admission built (in memory only).

    A call delivered again is matched against the earlier admission by its built arguments' digest (the
    invocation ledger, the results kept for replay), so building it again must give the same text. The
    transcript a base is read from can change in between: binding the connection's key redacts a row,
    and a later script can share an id. Without this, such a call was refused as invalid with an
    invitation to send the script again, instead of being answered as the replay it is.
    The map holds what was already admitted for those exact arguments, so it authorizes nothing new.
    """
    built = getattr(controller, "_script_store_built", None)
    if not isinstance(built, dict):
        built = {}
        try:
            controller._script_store_built = built
        except AttributeError:
            pass
    return built


def read(controller, arguments):
    """The ``angelo_script`` result: the script's id, description, code and line count."""
    description, code = find(controller, arguments.get("script"))
    return {"success": True, "read_only": True, "scene_changed": False, "script": arguments["script"],
            "description": description, "code": code, "lines": code.count("\n") + 1}


def validate(arguments):
    """``angelo_script``'s arguments, checked."""
    if not isinstance(arguments, dict) or set(arguments) != {"script"}:
        raise ValueError("angelo_script takes exactly script: an id a script's result named")
    if not isinstance(arguments["script"], str) or not re.fullmatch(SCRIPT_ID, arguments["script"]):
        raise ValueError("A script id is s and eight hex digits, as a script's result names it (script)")
    return dict(arguments)
