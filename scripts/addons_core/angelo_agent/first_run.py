# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the empty chat and first launch offer: connect options, examples, demos, notices.

Pure Python (no ``bpy``) so every rule is host-testable. Presentation lives in
``chat_welcome.py`` and ``intro.py``; the connect state machine in
``quick_connect.py``.

Detection reuses the native status checks that ``authentication.py`` already
runs at start-up (``claude auth status`` / ``codex login status``): a status of
"Native runtime unavailable" means the CLI was not found, anything else means it
is installed. ChatGPT needs no CLI where Angelo carries its agent runtime
(``built_in``): its sign-in then runs through that runtime. Discovery never
creates a connection by itself; a Connect button is the person's explicit choice.

Every string is a whole sentence or a named-placeholder template marked with
``n_`` and translated where it is drawn (``ui_text.py``). Nothing here is sent
to a model: notices are display-only and stored chat rows keep the provider's
original text.
"""

import re
import time

from .ui_text import iface_, n_

# Subscription sign-ins the chat can connect in one step, in display order.
# Strings are written out per provider, never "Connect " + name.
CONNECT = {
    "claude_subscription": {
        "provider": "ANTHROPIC", "cli": "Claude Code",  # i18n: product name matched against status text
        "title": n_("Claude subscription"), "connect": n_("Connect Claude"), "use": n_("Use Claude"),
        "signed_in": n_("Uses the Claude sign-in already on this computer"),
        "browser": n_("Opens Claude sign-in in your browser"),
        "install": n_("Install Claude Code first, then restart Angelo"),
        "retry": n_("Sign-in didn't finish. Click Connect Claude to try again."),
        "phases": {"checking": n_("Checking your Claude sign-in…"),
                   "signing_in": n_("Finish signing in to Claude in your browser…"),
                   "loading": n_("Loading Claude models…")}},
    "codex_subscription": {
        "provider": "OPENAI", "cli": "Codex",
        "title": n_("ChatGPT subscription"), "connect": n_("Connect ChatGPT"), "use": n_("Use ChatGPT"),
        "signed_in": n_("Uses the ChatGPT sign-in already on this computer"),
        "browser": n_("Opens ChatGPT sign-in in your browser"),
        "install": n_("Install the Codex CLI first, then restart Angelo"),
        "retry": n_("Sign-in didn't finish. Click Connect ChatGPT to try again."),
        "phases": {"checking": n_("Checking your ChatGPT sign-in…"),
                   "signing_in": n_("Finish signing in to ChatGPT in your browser…"),
                   "loading": n_("Loading ChatGPT models…")}},
}
ALREADY_CONNECTED = n_("Already connected · switch this chat to it")
API_KEY = {"label": n_("Use an API key"), "note": n_("Claude, OpenAI, Grok, MiniMax or Kimi, billed to that API account")}
NO_CLI = n_("Have a Claude or ChatGPT subscription? Install Claude Code or the Codex CLI, "
            "then restart Angelo to connect it here.")
MISSING_RUNTIME = "Native runtime unavailable"  # i18n: protocol status value from authentication.py, compared never shown

# Prompts that need only the default scene (Cube, Camera, Light) and natively
# covered work: a Bevel modifier and Principled material, duplicates,
# keyframes, and lights (doc/angelo/native_coverage.rst). The composer gets
# the translated text: it is the person's own prompt.
EXAMPLES = (
    n_("Round the cube's edges and make it brushed gold"),
    n_("Make a ring of eight small cubes around the cube"),
    n_("Animate the cube spinning once over two seconds"),
    n_("Light the cube with warm three-point lighting"),
)

# Then two that teach what Media Studio and Generation Flows do through the chat: a person can just ask (1.0.5; before
# it only the editor-type menu led there). Like the others a click fills the message box and Return sends it, never a
# click alone, since a generation spends the person's own generation account. They need no file or selection.
GENERATION_EXAMPLES = (
    n_("Generate a reference image of a cozy wooden cabin"),
    n_("Turn a reference image into a 3D model"),
)


def suggestions():
    """Every example the empty chat offers, in order: the scene examples, then the generation ones."""
    return EXAMPLES + GENERATION_EXAMPLES

# One quiet tip under the examples, a link to the website's docs section that
# explains it ("Spend less, get more" on the identity table's manual page). It
# stays true on every provider: no cache window and no benchmark figure here,
# those belong to the docs section, which says what each is measured against.
TIP = n_("Tip: small steps in one chat keep replies quick and costs down.")
TIP_URL = "https://angelo.studio/docs#spend-less"

# "See it in action" in the first-launch intro (intro.py). Placeholders until
# the recordings exist: replace only the URLs here.
DEMOS = (
    (n_("Build a bridge"), "https://angelo.studio/demos/bridge"),
    (n_("Bevel and shade a model"), "https://angelo.studio/demos/bevel-and-shade"),
    (n_("Give it a material"), "https://angelo.studio/demos/material"),
    (n_("Rig and pose a character"), "https://angelo.studio/demos/rig-pose"),
)


def cli_installed(status):
    """The start-up status check found the CLI (it may still be signed out)."""
    return bool(status) and not str(status).startswith(MISSING_RUNTIME)


def built_in(mode):
    """Angelo runs this mode's runtime itself: its agent runtime runs every Codex connection
    (``agent_runtime``), so ChatGPT is always offered and never needs a CLI installed."""
    from . import agent_runtime
    return mode == "codex_subscription" and agent_runtime.present()


def runtime_available(mode, status):
    """The connect card offers ``mode``: its CLI was found, or Angelo carries the runtime."""
    return cli_installed(status) or built_in(mode)


def connect_options(statuses, ready_modes=(), team=None):
    """The connect card's rows, in display order: [team], detected subscriptions, API key.

    Each row is ``{"id", "label", "note", "action", "value"}`` with untranslated
    message ids; ``chat_welcome`` translates and draws every row the same way.
    ``team`` is the slot for an organisation sign-in (``team_chat.welcome_row``):
    a row of the same shape, drawn first, which enterprise policy can make the
    only choice. It may add ``busy`` (a sign-in in progress, drawn as running)
    and ``cancel`` (the action of its Cancel button).
    """
    rows = [dict(team, id="team")] if team else []
    for mode, spec in CONNECT.items():
        status = statuses.get(mode)
        if not runtime_available(mode, status):
            continue
        connected = mode in ready_modes
        note = (ALREADY_CONNECTED if connected else
                spec["signed_in"] if str(status).startswith("Connected to ") else spec["browser"])
        rows.append({"id": mode, "label": spec["use" if connected else "connect"], "note": note,
                     "action": "welcome_connect", "value": mode})
    rows.append({"id": "api_key", "label": API_KEY["label"], "note": API_KEY["note"],
                 "action": "welcome_api_key", "value": None})
    return rows


def phase_text(phase, mode):
    """The message id a Connect button shows while ``phase`` is in progress."""
    return CONNECT[mode]["phases"][phase]


def should_show_intro(seen, mode_saved, disabled):
    """First launch only: no intro shown before and no Director/Manual choice saved by an older build."""
    return not (seen or mode_saved or disabled)


def splash_on_startup(enabled, file_opened, preferences_saved):
    """Blender's rule for opening the splash at startup (``wm_init_splash_show_on_startup_check``):
    when it is enabled in Preferences, unless a file was opened and preferences exist."""
    return enabled and not (file_opened and preferences_saved)


# Limit and connection failures as providers word them. Codex rows are
# prefixed by provider_errors.codex_error_text; Claude Code reports
# "...usage limit reached|<epoch>" (older) or "You've hit your ... limit · resets 4pm".
_LIMIT = re.compile(r"usage limit|rate limit|session limit|weekly limit|hit your (?:\w+ )?limit|limit reached"
                    r"|limit exceeded|quota exceeded|too many requests|usageLimitExceeded|rateLimitExceeded"
                    r"|sessionBudgetExceeded", re.IGNORECASE)
_OFFLINE = re.compile(r"connection error|connection (?:refused|reset|lost)|network (?:is )?unreachable"
                      r"|could not resolve|getaddrinfo|name or service not known|(?:request|connection) timed out|stream disconnected"
                      r"|error sending request|failed to connect|unable to connect|offline|ENOTFOUND|ECONNREFUSED",
                      re.IGNORECASE)
_EPOCH = re.compile(r"\|\s*(\d{10})\b")
_RESETS = re.compile(r"\b(?:resets?|try again)\s+(?:at\s+|in\s+|on\s+)?([^.;\n|]{1,60})", re.IGNORECASE)


def reset_time(text, now=None):
    """The reset moment the provider named, as display text, or "" when it gave none.

    An epoch (``...|1760040000``) becomes local clock time (with the date when it
    is not today); worded times ("4:30pm (UTC)", "Oct 9th, 2026 4:12 PM",
    "in 3 hours") are shown as the provider wrote them.
    """
    match = _EPOCH.search(text or "")
    if match:
        moment = time.localtime(int(match.group(1)))
        today = time.localtime(time.time() if now is None else now)
        # Numeric, so no English month name reaches another language.
        return time.strftime("%H:%M" if moment[:3] == today[:3] else "%Y-%m-%d %H:%M", moment)
    match = _RESETS.search(text or "")
    if match:
        value = " ".join(match.group(1).split()).rstrip(" ,")
        return value[:40]
    return ""


def notice(text):
    """A readable headline for a provider limit or connection failure, or None.

    ``{"kind": "limit"|"offline", "headline": str, "reset": str}``; the headline
    is translated, so call this when drawing. The reset time stays as the
    provider wrote it. Only error rows are classified; a model's ordinary reply
    never is.
    """
    if not isinstance(text, str) or not text:
        return None
    if _LIMIT.search(text):
        reset = reset_time(text)
        headline = (iface_("Usage limit reached · resets {time}", time=reset) if reset
                    else iface_("Usage limit reached"))
        return {"kind": "limit", "reset": reset, "headline": headline}
    if _OFFLINE.search(text):
        return {"kind": "offline", "reset": "", "headline": iface_("Can't reach the provider · check your internet connection")}
    return None


def voice_supported(enabled, platform, provider, audio_tools_ready):
    """Voice is opt-in at 1.0 (Experimental voice preference), and relays through Codex
    realtime with macOS AVFoundation via ffmpeg/ffplay (voice.py).

    Anywhere else the microphone control would be dead, so the chat hides it.
    """
    return (bool(enabled) and platform == "darwin" and str(provider or "").startswith("codex_")
            and bool(audio_tools_ready))
