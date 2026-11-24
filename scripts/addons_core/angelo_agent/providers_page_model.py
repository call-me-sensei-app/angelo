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
from .ui_text import iface_, join, n_, plural_

_was_ready = set()   # Connection ids seen ready this session: a later "Not connected" reads as an expired sign-in.

TITLES = {CODING: n_("Coding agents"), GENAI: n_("Media generation")}
DESCRIPTIONS = {CODING: n_("Models that work in Angelo with you. Every connected account is a choice in the chat "
                           "and can be a collaborator."),
                GENAI: n_("Image, 3D and video providers used by Media Studio and Generation Flows")}
CLAUDE_ROUTE_TITLE = n_("Claude Code follows your administrator's network settings")
CLAUDE_ROUTE_LINE = n_("This computer's Claude Code policy sets {names}. On team connections Claude Code's traffic then "
                       "follows those settings instead of going only to your organization's gateway.")
FORGET_CHANGES = n_("Forget Agent Changes in a Folder…", "Operator")   # agent_changes_forget's operator label.
# What its Browse does: the operator's own description (chat_destinations.FORGET_CHANGES_DOES; bpy-free here too).
FORGET_CHANGES_DOES = n_("Load the code in a folder you choose as your own again: Angelo forgets which of its files "
                         "agent scripts wrote, moved or linked. Only your own click can do this")
DESCRIPTION_NO_COLLABORATORS = n_("Models that work in Angelo with you. Every connected account is a choice in the chat.")


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
    from . import saved_keys_ui, version_carry_over
    notices = []
    if policy.managed:
        from . import policy_notes
        lines = [shown(note) for note in policy_notes.organization(policy)]
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
    from . import policy_notes
    for note in policy_notes.own_file(policy):   # The person's own policy file, managed policy or not (its sentence).
        notices.append({"title": shown(note), "lines": [], "glyph": "lock"})
    for title, notice in version_carry_over.attention_notices():
        notices.append({"title": iface_(title), "lines": [shown(notice)]})   # A Text keeps its fields.
    for problem in saved_keys_ui.notices():
        notices.append({"title": shown(problem), "lines": []})
    from . import claude_managed, connection_access
    routes = claude_managed.env_overrides() if connection_access._team().signed_in() else []
    if routes:   # The administrator's own Claude Code policy overrides the team fence (team_network).
        notices.append({"title": iface_(CLAUDE_ROUTE_TITLE), "lines": [iface_(CLAUDE_ROUTE_LINE, names=join(routes))],
                        "tone": "muted", "glyph": "info"})
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
    from . import script_access   # The switch's text holds SENTENCE; its button opens the limits (script_limits).
    row = {"kind": "switch", "name": name, "lines": [text], "on": prefs.always_allow_scripts and not locked,
           "action": "toggle", "value": "always_allow_scripts", "enabled": not locked,
           "buttons": [(iface_(script_access.LINK), "script_limits", "", "ghost")]}
    if locked:
        row["lock"] = shown(Text(managed_policy.AUTO_APPROVAL_OFF))
    elif prefs.always_allow_scripts:   # A consent from before 2.0.1 does not cover connected apps' chats yet.
        from . import always_allow
        row["warning"] = (iface_("On: scripts in Auto chats run without asking.") if always_allow.covers_external()
                          else iface_("On for Angelo's own chats. Connected apps' chats still ask until you press "
                                      "Always allow on one of their script cards."))
    rows.append(row)
    # Forget Agent Changes in a Folder (agent_changes_forget): its operator's label and what its click does; Browse
    # runs it in the press's grant scope (providers_page_host.act), as its Preferences button does.
    rows.append({"kind": "connection", "glyph": "info", "name": iface_(FORGET_CHANGES, "Operator"),
                 "lines": [iface_(FORGET_CHANGES_DOES)],
                 "buttons": [(iface_("Browse"), "forget_agent_changes", "", "ghost")]})
    if "strict_script_protection" in prefs.bl_rna.properties:   # Off by default: it can slow agent scripts (Ruling 17).
        name, text = describe("strict_script_protection")
        rows.append({"kind": "switch", "name": name, "lines": [text], "on": prefs.strict_script_protection,
                     "action": "toggle", "value": "strict_script_protection"})
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


WORKER_CHOICES = (1, 2, 4, 8, 16)   # Workers at once beside Suggested; 16 is parallel_capability.MAX_WORKERS.


def _parallel_edit(prefs):
    """Whether this computer runs parallel edit (``parallel_capability``): how many workers at once, chosen by the person
    (``parallel_setting``: Suggested follows the machine) except in a forced launch, which keeps its own; or what it
    needs. Whenever parallel edit runs, the row after it sets where the Sessions panel stands (``providers_page_rail``)."""
    from . import parallel_access, parallel_capability
    machine = parallel_capability.current()
    row = {"kind": "connection", "glyph": "info", "lines": []}
    rows = [row]
    if parallel_access.enabled():
        row["name"] = plural_(parallel_access.worker_limit(), "Up to {count} worker agent at once on this computer.",
                              "Up to {count} worker agents at once on this computer.")
        if prefs is not None and parallel_access._forced() != "1":
            chosen = parallel_access.chosen()
            row["lines"], row["name"] = [row["name"]], iface_("Workers at once")
            if chosen > machine.workers:
                row["facts"] = [("alert", iface_("More than this computer suggests: other apps may slow down, or "
                                                 "workers may fail to start."), "amber")]
            offered = sorted(set(WORKER_CHOICES) | ({chosen} if chosen else set()))   # A stored 3 shows, chosen.
            row["choices"] = [(iface_("Suggested ({count})", count=machine.workers) if count == 0 else str(count),
                               "pref", ("parallel_workers", count), count == chosen)
                              for count in [0] + offered]
        if prefs is not None:   # Beside it, the Sessions panel's place: the person's, in a forced launch too.
            from . import providers_page_rail
            rows.append(providers_page_rail.row())
    elif machine.capable:
        row["name"] = iface_("Turned off for this launch (ANGELO_ENABLE_PARALLEL_AGENTS=0).")
    else:   # What this computer lacks, and only that.
        needs = {"gpu": iface_("a GPU"),
                 "memory": iface_("{memory} GB of memory", memory=parallel_capability.required_memory_gb()),
                 "cores": iface_("{cores} processor cores", cores=parallel_capability.MIN_CORES)}
        row["name"] = iface_("Parallel edit needs {what}, which this computer doesn't have.",
                             what=join([needs[name] for name in machine.missing], separator=", "))
    return {"title": iface_("Parallel edit"),
            "description": iface_("The agent can split a large job into parts that worker agents build at the same "
                                  "time, each shown live in the Sessions panel."),
            "rows": rows}


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
    from .providers_page_endpoints import section as endpoint_section   # The organization's own servers first.
    endpoints = endpoint_section()
    if endpoints:
        sections.append(endpoints)
    first_run = personal and not coding and not endpoints
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
    sections.append(_parallel_edit(prefs))
    sections.append(_enhance(ui))
    from . import fab_ui
    sections.append(fab_ui.preferences_section(prefs))
    from . import providers_page_external
    sections.append(providers_page_external.section(ui, prefs, policy))
    hero = {"title": iface_("AI Providers"), "banner": banner_ready(),
            "purpose": iface_("The accounts Angelo's agent works with. Each chat uses the one you choose, never a fallback.")}
    if personal:
        hero["add"] = iface_("Add connection")
        hero["add_style"] = "secondary" if first_run else "primary"
    return {"hero": hero, "attention": _attention(ui, policy), "team": team, "sections": sections,
            "trust": iface_("Keys are never written to preferences, .blend files, logs or chats. Removing a connection "
                            "erases its saved key, not your provider account.")}


def banner_ready():
    """The hero keeps the raven banner's place unless the art is missing (an undownloaded asset: the drawn mark).
    The page shows the welcome screen's picture while its input runs (``providers_page_host``): read then, outside
    drawing and with no image datablock (``brand_picture``), so nothing closes a dialog over Preferences, and let go
    when the page is left; the art is drawn as soon as it is read."""
    from . import welcome_screen
    return not welcome_screen.BANNER_PICTURE.failed


PAGE = "ai_providers_page"   # The page's name among the surfaces that show the banner (brand_picture.Picture.show).


def show_art(window):
    """The page's input started (outside drawing): read the art unless another surface holds it, and redraw the
    page once if its first draw went without it. Only here: no dialog is open over the page before its input runs,
    and a later catch-up redraw would close one (providers_page_host.draw_panel)."""
    from . import live_windows, welcome_screen
    picture = welcome_screen.BANNER_PICTURE
    drawn = picture.ready
    picture.show(PAGE)
    if picture.ready and not drawn:
        for area in live_windows.areas(window):
            if area.type == 'PREFERENCES':
                area.tag_redraw()


def art_settled():
    """The page's art is read and held while the page shows it, or is missing (the drawn mark stands in)."""
    from . import welcome_screen
    picture = welcome_screen.BANNER_PICTURE
    return picture.failed or (picture.ready and PAGE in picture.shown_by())


def hide_art():
    """The page was left (its input ended): its texture goes with the last surface that shows the art."""
    from . import welcome_screen
    welcome_screen.BANNER_PICTURE.hide(PAGE)


def sentence(text):
    """A property description (a Blender tooltip, no final stop) as a sentence on the page."""
    text = text.strip()
    return text if not text or text[-1] in ".!?…:" else text + "."


def ui_module():
    return sys.modules.get(f"{__package__}.ui")


def summary(model):
    """Closed page facts retained by the host for UI inspection; no connection objects or state mutation."""
    return {"sections": [s["title"] for s in model.get("sections", ())],
            "rows": [r.get("name") for s in model.get("sections", ()) for r in s.get("rows", ())],
            "chips": [r["chip"][0] for s in model.get("sections", ()) for r in s.get("rows", ()) if r.get("chip")],
            "team": (model.get("team") or {}).get("title"), "slim_team": bool((model.get("team") or {}).get("slim")),
            "attention": [n["title"] for n in model.get("attention", ())], "add": model["hero"].get("add")}
