# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The AI Providers page as data: every section, row, chip and action, read from the existing owners.

``build(ui)`` returns the model ``providers_page_layout.layout`` draws; every string is translated here
(``iface_`` / ``display_text.shown``), the provider's status line verbatim. ``readiness`` is the pure
status-to-chip table. Actions are ``(action, value)`` pairs that ``providers_page_host.act`` runs through
the same operators the stock page used.
"""

import sys

from . import managed_policy
from .display_text import Text, shown
from .provider_registry import CODING, GENAI, PROVIDER_NAMES, billing_label, plan_terms, spec_for
from .ui_text import iface_, n_

_was_ready = set()   # Connection ids seen ready this session: a later "Not connected" reads as an expired sign-in.

TITLES = {CODING: n_("Coding agents"), GENAI: n_("Generative AI")}
DESCRIPTIONS = {CODING: n_("Models that work in Blender with you. Every connected account is a choice in the chat "
                           "and can be a collaborator."),
                GENAI: n_("Your own API keys for image and 3D generation, used from the chat and the Generative AI "
                          "editor.")}
DESCRIPTION_NO_COLLABORATORS = n_("Models that work in Blender with you. Every connected account is a choice in the chat.")


def readiness(ready, status, group, was_ready=False):
    """``(label, tone, spinner)`` for a connection's chip, from data the page already holds."""
    if ready:
        return (n_("Available in chat") if group == CODING else n_("Key verified")), "teal", False
    low = status.lower()
    if "checking" in low or "signing in" in low or "verifying" in low or "complete sign-in" in low:
        return n_("Checking…"), "muted", True
    if "expired" in low or (was_ready and low.startswith("not connected")):
        return n_("Sign-in expired"), "amber", False
    if "rejected" in low or "invalid" in low or "unauthorized" in low or "401" in low:
        return n_("Key rejected"), "coral", False
    if "credit" in low or "quota" in low or "balance" in low:
        return n_("Needs credits"), "amber", False
    if "approval" in low or "approve" in low:
        return n_("Needs approval"), "amber", False
    if "not verified" in low or "deferred" in low or "configured" in low:
        return n_("Not checked yet"), "muted", False
    return n_("Not connected"), "muted", False


def _fix(chip, record, ui):
    label = chip[0]
    if label == n_("Sign-in expired") or (label == n_("Not connected") and record["method"] == "SUBSCRIPTION"):
        return iface_("Sign in again"), "reconnect", record["id"]
    if label in {n_("Key rejected"), n_("Not connected")}:
        return iface_("Replace key…"), "edit", record["id"]
    return None


def _connection_row(ui, record, group):
    from . import saved_keys_ui
    ready = ui.connection_ready(record)
    status = shown(ui.connection_status(record))
    if ready:
        _was_ready.add(record["id"])
    label, tone, spin = readiness(ready, str(ui.connection_status(record)), group, record["id"] in _was_ready)
    facts = []
    if group == CODING:
        models = ui.catalogs().entries.get(record["id"], [])
        default = record.get("model") or ""
        name = next((m.get("name") for m in models if m.get("id") == default), default)
        facts.append(("model", iface_("{model} by default", model=name) if name else iface_("Runtime default"), "muted"))
        if models:
            facts.append(("list", iface_("1 model in chat") if len(models) == 1 else
                          iface_("{count} models in chat", count=len(models)), "muted"))
    storage = saved_keys_ui.storage_label(record, bool(ui._memory_keys.get(record["id"])))
    if storage:
        text = shown(storage)
        facts.append(("lock", text, "amber" if "memory" in str(storage).lower() or "fail" in str(storage).lower() else "muted"))
    result = ui._api_connections.results.get(record["id"])
    if result and result.balance is not None:
        facts.append(("coins", iface_("{balance} credits available", balance=f"{result.balance:,.0f}"
                                      if float(result.balance).is_integer() else format(result.balance, "g")), "teal"))
    terms = plan_terms(record["mode"])
    if terms:
        facts.append(("info", iface_(terms["note"]), "muted"))
    chip = (iface_(label), tone, spin)
    return {"kind": "connection", "tile": PROVIDER_NAMES[record["provider"]][:1].upper(), "dot": tone if tone != "muted" or not ready else "teal",
            "name": record["name"], "tag": iface_(billing_label(record["mode"])),
            "lines": [iface_("{provider} · {status}", provider=PROVIDER_NAMES[record["provider"]], status=status)],
            "facts": facts, "chip": chip, "fix": None if ready else _fix((label, tone), record, ui),
            "icons": [("pencil", "edit", record["id"]), ("more", "menu", record["id"])]}


def connect_rows(ui, store):
    """One row per subscription the chat's welcome card offers, with its words: Connect Claude / Connect ChatGPT
    and whether it reuses this computer's sign-in or opens the provider's own in the browser, and no more (a
    "found" chip would repeat the note and squeeze it). Creates nothing."""
    from . import first_run, native_suggestions
    rows = []
    for offer in native_suggestions.connect_offers(store, ui._authentication.status):
        mode, spec = offer["mode"], first_run.CONNECT[offer["mode"]]
        rows.append({"kind": "connection", "glyph": "spark", "name": iface_(spec["title"]),
                     "lines": [iface_(spec["signed_in"] if offer["signed_in"] else spec["browser"])],
                     "buttons": [(iface_(spec["connect"]), "quick_connect", mode, "primary"),
                                 (iface_("Dismiss"), "dismiss_suggestion", mode, "ghost")]})
    return rows


def _attention(ui, policy):
    from . import data_migration, saved_keys_ui, state_migration
    notices = []
    if policy.managed:
        lines = []
        if policy.problems:
            from . import team_account_ui
            lines.append(shown(Text(team_account_ui.MANAGED_PROBLEM, keys=", ".join(policy.problems))))
        if not policy.allow_personal_connections:
            lines.append(shown(Text(managed_policy.PERSONAL_OFF)))
        lines.append(shown(managed_policy.contact(policy)))
        locks = [label for flag, label in (
            (not policy.allow_auto_approval, iface_("Always allow scripts")),
            (policy.require_ask_for_edits, iface_("Auto · bounded edits")),
            (not policy.allow_local_cli_access, iface_("External AI apps")),
            (not policy.allow_collaborators, iface_("Collaborators")),
            (not policy.allow_personal_connections, iface_("Personal connections")),
            (not policy.allow_voice, iface_("Voice"))) if flag]
        from . import team_account_ui
        notices.append({"title": shown(Text(team_account_ui.MANAGED)), "lines": lines, "locks": locks, "glyph": "lock"})
    for notice in (data_migration.attention_message(), state_migration.attention_message()):
        if notice:
            notices.append({"title": iface_("A pre-rename folder needs attention"), "lines": [iface_(notice)]})
    for problem in saved_keys_ui.notices():
        notices.append({"title": shown(problem), "lines": []})
    from . import agent_runtime
    if agent_runtime.override() is not None:
        notices.append({"title": iface_("Agent runtime override: ANGELO_AGENT_RUNTIME={value}", value=agent_runtime.override()),
                        "lines": [], "tone": "muted", "glyph": "info"})
    return notices


def _permissions(prefs, policy):
    def describe(name):
        prop = prefs.bl_rna.properties[name]
        return iface_(prop.name), sentence(iface_(prop.description))
    rows = []
    name, text = describe("helper_standing_consent")
    rows.append({"kind": "switch", "name": name, "lines": [text], "on": prefs.helper_standing_consent,
                 "action": "toggle", "value": "helper_standing_consent"})
    name, text = describe("always_allow_scripts")
    locked = not policy.allow_auto_approval
    row = {"kind": "switch", "name": name, "lines": [text], "on": prefs.always_allow_scripts and not locked,
           "action": "toggle", "value": "always_allow_scripts", "enabled": not locked}
    if locked:
        row["lock"] = shown(Text(managed_policy.AUTO_APPROVAL_OFF))
    elif prefs.always_allow_scripts:
        row["warning"] = iface_("On: scripts in Auto chats run without asking.")
    rows.append(row)
    if policy.require_ask_for_edits:
        rows.append({"kind": "switch", "name": iface_("Auto · bounded edits"), "on": False, "enabled": False,
                     "action": "noop", "value": "", "lock": iface_("Turned off by your organization. Every chat uses Ask before changes.")})
    return {"title": iface_("Agent permissions"), "description": iface_("What the agent may do without asking you first."),
            "rows": rows}


def _background_jobs(prefs):
    """The ``job_desktop`` preferences (``job_desktop_prefs``): the computer kept awake, a long job's end announced."""
    from .job_desktop import PREFERENCES
    rows = []
    properties = getattr(getattr(prefs, "bl_rna", None), "properties", {})
    for value in PREFERENCES:
        prop = properties.get(value)
        if prop is None:   # Preferences without them (an older add-on build): no row rather than an error.
            continue
        rows.append({"kind": "switch", "name": iface_(prop.name), "lines": [sentence(iface_(prop.description))],
                     "on": bool(getattr(prefs, value)), "action": "toggle", "value": value})
    return {"title": iface_("Background jobs"),
            "description": iface_("While a render, bake or export runs, also when you are away."), "rows": rows}


def _enhance(ui):
    from . import studio
    config = studio.library().enhancer
    missing = False
    if config["connection"] == "AUTO_GEMINI":
        name = iface_("Gemini · shared Nano Banana key")
    else:
        found = ui.find_connection(config["connection"])
        missing = found is None
        name = (found or {}).get("name", iface_("Reconnect selected account"))
    row = {"kind": "connection", "glyph": "spark", "name": name, "lines": [config["model"]],
           "facts": [("info", iface_("Prompt enhancement only. API keys stay in their existing connections."), "muted")],
           "buttons": [(iface_("Configure…"), "enhancer", "", "secondary")]}
    if missing:
        row["chip"] = (iface_("Account removed"), "amber", False)
    return {"title": iface_("AI Enhance"), "description": iface_("Rewrites image and 3D prompts for you to review before they are used."),
            "rows": [row]}


def build(ui, prefs):
    """The whole page. ``prefs`` is None when the add-on is disabled (one card with Enable)."""
    if prefs is None:
        return {"hero": {"title": iface_("AI Providers"), "purpose": iface_("Enable Angelo's agent to manage AI connections."),
                         "add": iface_("Enable"), "add_action": "enable"}, "sections": []}
    from . import team_account_ui
    policy = managed_policy.current()
    store = ui.connections()
    personal = policy.allow_personal_connections
    team = __import__(f"{__package__}.providers_page_team", fromlist=["model"]).model()
    personal = personal and not (policy.require_team_sign_in and not (team_account_ui._account and team_account_ui._account.view().signed_in))
    records = list(store.records.values())
    coding = [r for r in records if spec_for(r["mode"])["group"] == CODING]
    sections = []
    first_run = personal and not coding
    if personal:
        suggestions = connect_rows(ui, store)
        if first_run:
            rows = suggestions + [{"kind": "connection", "glyph": "plus", "name": iface_("Use an API key"),
                                   "lines": [iface_("Anthropic, OpenAI, Gemini, Grok and more. API usage is billed to your API account.")],
                                   "buttons": [(iface_("Add a key…"), "add", CODING, "secondary")]}]
            sections.append({"title": iface_("Get started"), "description": iface_("Connect an account to start chatting. "
                             "Nothing is added until you choose."), "rows": rows})
        for group in (CODING, GENAI):
            rows = [] if group != CODING or first_run else list(suggestions)
            rows += [_connection_row(ui, r, group) for r in records if spec_for(r["mode"])["group"] == group]
            description = DESCRIPTIONS[group] if group == GENAI or policy.allow_collaborators else DESCRIPTION_NO_COLLABORATORS
            section = {"title": iface_(TITLES[group]), "description": iface_(description), "rows": rows,
                       "add": (iface_("Add"), "add", group)}
            if not rows and group == CODING:
                section["empty"] = iface_("No coding agent yet. Connect one above, or add an API key.")
            if group == GENAI and not rows:
                section["chips"] = [(iface_("Add a key for {service}", service=name), "add", GENAI)
                                    for name in ("Gemini", "Tripo", "Meshy")]
            sections.append(section)
    sections.append(_permissions(prefs, policy))
    sections.append(_background_jobs(prefs))
    sections.append(_enhance(ui))
    from . import providers_page_external
    sections.append(providers_page_external.external_apps(ui, prefs, policy))
    hero = {"title": iface_("AI Providers"), "banner": banner_ready(),
            "purpose": iface_("The accounts Angelo's agent works with. Each chat uses the one you choose, never a fallback.")}
    if personal:
        hero["add"] = iface_("Add connection")
        hero["add_style"] = "secondary" if first_run else "primary"
    return {"hero": hero, "attention": _attention(ui, policy), "team": team, "sections": sections,
            "trust": iface_("Keys are never written to preferences, .blend files, logs or chats. Removing a connection "
                            "erases its saved key, not your provider account.")}


ART = {"pixels": None, "size": None, "texture": None, "done": False}   # The hero's own reference to the banner's pixels.


def banner_ready():
    """The raven banner's pixels are held (read once at start-up by ``preload_banner``, never while drawing)."""
    return ART["pixels"] is not None


def preload_banner():
    """A start-up timer: read the welcome screen's banner once and keep a reference, so the page never adds and
    removes an image datablock while it is open (that closes a dialog over Preferences), and the welcome
    screen's ``release`` cannot take the art away from the page."""
    from . import welcome_screen
    welcome_screen.prepare_banner()
    if welcome_screen._banner["pixels"] is not None:
        ART.update(pixels=welcome_screen._banner["pixels"], size=welcome_screen._banner["size"], texture=None)
    ART["done"] = True   # Read (or found missing) once: from here on the page never touches bpy.data.images.
    return None


def sentence(text):
    """A property description (a Blender tooltip, no final stop) as a sentence on the page."""
    text = text.strip()
    return text if not text or text[-1] in ".!?…:" else text + "."


def ui_module():
    return sys.modules.get(f"{__package__}.ui")
