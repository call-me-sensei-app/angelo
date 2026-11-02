# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Content proof that a picture reached the model on Angelo's Codex runtime (``turn/modelInputImages``).

Codex's code mode hands a tool's pictures to the model's script, which forwards
them only if it calls ``image()``; stock Codex reports nothing that tells the
two apart, so those pictures were recorded as unconfirmed (``request_path``).
Angelo's agent runtime (``agent_runtime``) adds one app-server notification,
sent once for every model request the provider accepted, listing each inline
picture new in that request's input::

    turn/modelInputImages {threadId, turnId, responseId,
                           images: [{callId, sha256, sourceSha256?}]}

``sha256`` is the SHA-256 of the picture's decoded bytes; ``sourceSha256``,
set when the runtime re-encoded (resized) it, is that of the bytes as it
received them. Angelo hashes the exact bytes it hands over (each tool result's
``data:`` URL, each attachment's file) and a report delivers the picture whose
bytes it names (``picture_ledger``): by content only, never by tool, question
or kind, so views, job pictures, sculpt views, previews, media and the person's
attachments are all covered alike.

* ``notification`` is the only reader of the report's field names (``FIELDS``):
  the app-server v2 convention is camelCase, and a snake_case spelling is
  accepted too, so a casing change in the fork cannot quietly disable the proof.
* ``supported`` is the gate: the process is Angelo's agent runtime (bundled or
  an override, as ``codex_connect.stamp_route`` reads it; a worker chat's from
  Angelo's word) and its pin lists the notification
  (``agent_runtime.json``'s ``notifications``, which the release check confirms
  in the build's app-server schema). Any other Codex keeps the previous record:
  ``images_unconfirmed``, ``image_delivery`` ``unknown``.
* ``observe`` admits a report only for the request's own thread and turn (the
  turn's scope, as every item event; a report without ``turnId`` only while
  that turn is open) and under the request's fence; a response reported twice
  delivers nothing more.

Code mode can also show a tool's pictures without ``image()``: a build whose
pin lists the launch setting ``runtime_launch.FORWARD_IMAGES_KEY`` (the pin's
``config_keys``) forwards the pictures of the dynamic tools that setting lists
by itself, once per turn per picture (``forwards_images``). Angelo lists every
tool whose result can carry pictures (``image_tools``, by the same predicate
``providers.tool_content`` packs them by, ``returns_images``) and tells the
model ``image()`` is optional there (``codex_guidance``); any other Codex is
launched and told exactly as before. Whatever forwards a picture, its delivery
is still proven only by the report above.

Standard library only; ``request_path`` and the tool registries are imported on
use (``request_path`` imports ``references``, which hashes through this module).
"""

import base64
import binascii
import hashlib
import re

METHOD = "turn/modelInputImages"
MAX_IMAGES = 64  # Read from one report; Angelo hands at most six pictures to one call.
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
# The report's field names, in this one place (module docstring).
FIELDS = {"thread": ("threadId", "thread_id"), "turn": ("turnId", "turn_id"), "response": ("responseId", "response_id"),
          "images": ("images",), "call": ("callId", "call_id"), "sha256": ("sha256", "sha_256"),
          "source": ("sourceSha256", "source_sha256")}


def _field(values, name):
    return next((values[key] for key in FIELDS[name] if key in values), None)


def _digest_field(value):
    value = value.lower() if isinstance(value, str) else None
    return value if value and _DIGEST.match(value) else None


def notification(params):
    """``{"thread", "turn", "response", "images": [(sha256, source sha256 or None)]}``, or None when malformed.

    An image entry without a valid ``sha256`` is skipped; one listed twice in a report counts once.
    """
    if not isinstance(params, dict) or not isinstance(_field(params, "images"), list):
        return None
    images, seen = [], set()
    for image in _field(params, "images")[:MAX_IMAGES]:
        if not isinstance(image, dict):
            continue
        sha, source = _digest_field(_field(image, "sha256")), _digest_field(_field(image, "source"))
        key = (str(_field(image, "call")), sha, source)
        if sha and key not in seen:
            seen.add(key)
            images.append((sha, source))
    text = lambda name: _field(params, name) if isinstance(_field(params, name), str) else None  # noqa: E731
    return {"thread": text("thread"), "turn": text("turn"), "response": text("response"), "images": images}


def digest(data):
    """SHA-256 of ``data`` as the report spells it: 64 lowercase hexadecimal digits."""
    return hashlib.sha256(data).hexdigest()


def url_digest(url):
    """SHA-256 of the bytes a base64 ``data:`` URL carries, or None for any other value."""
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    header, comma, body = url.partition(",")
    if not comma or not header.endswith(";base64"):
        return None
    try:
        return digest(base64.b64decode(body, validate=True))
    except (binascii.Error, ValueError):
        return None


def supported(provider):
    """Whether ``provider``'s Codex process reports the pictures in its model input (module docstring)."""
    from . import agent_runtime
    from .runtime_relay import launched
    lead = launched(getattr(provider, "rpc", None))  # A worker chat: the Codex Angelo launched for it.
    kind = lead["kind"] if lead is not None else getattr(provider, "_runtime_kind", None)
    return kind in ("bundled", "override") and METHOD in agent_runtime.PIN.get("notifications", ())


def begin(provider, context):
    """A text turn of ``context``'s request starts on ``provider``: record whether its pictures are proven."""
    from .request_path import prove
    prove(context, not getattr(context, "voice", None) and supported(provider))


def _admitted(provider, context, report):
    scope = provider._scope
    ids = {"threadId": report["thread"], "turnId": report["turn"]}
    if report["turn"] is not None:
        return scope.matches(context, ids)
    with scope.lock:  # The thread has one open turn: a report without its ID belongs to it while it is open.
        return scope.matches(context, ids, turn_required=False) and bool(scope.turn_id) and not scope.completed


def observe(provider, context, params):
    """A ``turn/modelInputImages`` report: the pictures it names are delivered (module docstring)."""
    report = notification(params)
    if report is None or not report["images"] or not _admitted(provider, context, report):
        return
    from .request_path import confirmed
    confirmed(context, report["images"], report["response"])


# Pictures code mode shows without image() ---------------------------------------------------------------------------

MEDIA_PREFIXES = ("angelo_studio_", "angelo_team_", "angelo_library_")  # Media families: results that are pictures.
# The sentence of the Codex tool guidance that says how a result's pictures are shown, and the same fact on a runtime
# that forwards them itself. The rest of the guidance is unchanged.
SHOWN_BY_IMAGE = "adds one data:image URL per further line, which image(url) shows you (its second\nargument"
SHOWN_ALWAYS = ("adds one data:image URL per further line, each shown to you automatically;\nimage(url) is optional "
                "(its second argument")


def returns_images(name):
    """Whether a result of tool ``name`` can carry pictures (``providers.tool_content`` packs them for these only)."""
    from .claude_evidence import VISUAL_IMAGE_TOOLS
    from .plan_recovery import PREVIEW_IMAGE_TOOLS
    return name in VISUAL_IMAGE_TOOLS or name in PREVIEW_IMAGE_TOOLS or name.startswith(MEDIA_PREFIXES)


def image_tools():
    """Every declared tool whose results can carry pictures, by the name it is declared with, in registry order."""
    from .tool_specs import TOOLS
    return tuple(tool["name"] for tool in TOOLS if returns_images(tool["name"]))


def forwarding_build(kind):
    """Whether a Codex of ``kind`` (``agent_runtime.KINDS``) forwards listed tools' pictures in code mode itself."""
    from . import agent_runtime
    from .runtime_launch import FORWARD_IMAGES_KEY
    return kind in ("bundled", "override") and FORWARD_IMAGES_KEY in agent_runtime.PIN.get("config_keys", ())


def forwards_images(provider):
    """Whether ``provider``'s Codex process forwards its tools' pictures itself (``forwarding_build``)."""
    from .runtime_relay import launched
    lead = launched(getattr(provider, "rpc", None))  # A worker chat: the Codex Angelo launched for it.
    return forwarding_build(lead["kind"] if lead is not None else getattr(provider, "_runtime_kind", None))


def codex_guidance(text, forwarded):
    """The Codex tool guidance ``text`` as it is true of the runtime: unchanged unless it ``forwarded`` pictures."""
    if not forwarded:
        return text
    return text.replace(SHOWN_BY_IMAGE, SHOWN_ALWAYS)
