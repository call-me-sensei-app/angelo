# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Connections belong in settings/welcome, never on the chat's editor chrome."""

import json
import os
import shutil
import shlex
import sys
import uuid
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Menu, Operator

from . import console, docking, preferences_ui, chat_popovers, chat_titles, chat_view, saved_keys_ui, state_paths, native_suggestions, team_account_ui
from . import agent_runtime, script_errors, text_layout
from .pump_guard import keep_alive
from .connection_access import (collaborators_allowed, connection_records, find_connection, gemini_text_binding,  # noqa: F401
                                gemini_text_ready, generation_binding, generation_health, remember_preferred_mode)
from .chat_changes import scene_change_report
from .authentication import NativeAuthentication, native_auth_command
from .studio_spec import STUDIO_NAMES
from .blender_tools import BlenderExecutor, track_scene_changes
from .controller import Controller, pump_controller_tools
from .sessions import SessionCollection
from .connections import ConnectionStore
from .chat_settings import clean_options, selected_model
from .catalogs import ModelCatalogs
from .api_connections import ApiConnections, clean_key
from .provider_registry import (CODING, GENAI, GROUPS, SPECS, MODE_BY_CONNECTION, plan_terms, billing_label, runtime_kind,
    HTTP_MODES, CUSTOM_PROVIDER_MODES, PROVIDER_NAMES, provider_items, methods, is_coding, is_team, spec_for)
from .references import FILE_FILTER, FILE_TYPES
from .storage import connection_directory as persistent_connection_directory
from .display_text import Text, shown
from .ui_text import iface_, n_, notice, rpt_
from .trusted_scripts_ui import (ANGELO_OT_trusted_scripts, ANGELO_MT_approval, ANGELO_OT_set_approval, always_allow_get,
                                 always_allow_set, draw_always_allow)

_controller = None
_document_serial = str(uuid.uuid4())
_main_window_pointer = None
_dock_area_pointer = None
_header_draw = None
_header_states = {}
_keymaps = []
_memory_keys = {}
_layout_timer = None
_dock_side = "RIGHT"
_dock_metadata = {}
_authentication = NativeAuthentication()
_api_connections = ApiConnections()
_sessions = None
_controllers = {}
_tool_pump_cursor = 0
_drafts = {}
_transient_preferences = False
_connections = None
_catalogs = None
_restoring_snapshot = False
_capturing_checkpoint = False
_recovery_original_path = ""

PROVIDERS = [
    ("codex_subscription", n_("ChatGPT / Codex subscription"), n_("Use the official Codex runtime and native ChatGPT login")),
    ("codex_api", n_("OpenAI API key"), n_("Usage is billed to your OpenAI API account")),
    ("claude_subscription", n_("Claude subscription"), n_("Sign in through the unmodified Claude Code binary")),
    ("claude_api", n_("Claude API key"), n_("Usage is billed to your Claude API account")),
    ("grok_api", n_("Grok API key"), n_("Grok through the Codex runtime, billed to your xAI account")),
    ("minimax_api", n_("MiniMax API key"), n_("MiniMax M3 through the Codex runtime")),
    ("kimi_api", n_("Kimi API key"), n_("Kimi K3 through the Codex runtime")),
]


def preferences():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def main_window():
    windows = list(bpy.context.window_manager.windows)
    return next((w for w in windows if w.as_pointer() == _main_window_pointer), windows[0] if windows else None)


def document_key():
    window = main_window()
    # Native ID is stable across Undo and a scene rename; Save As is not a new document.
    return (_document_serial, window.scene.session_uid if window else 0)


def state_directory():
    override, _variable = state_paths.state_override()
    if override is not None:
        return str(override)
    return bpy.utils.user_resource("CONFIG", path=state_paths.STATE_FOLDER, create=True)


def connections():
    global _connections
    if _connections is None:
        _connections = ConnectionStore(connection_directory())
    return _connections


def connection_directory():
    return str(persistent_connection_directory())


def connection_record(controller=None):
    controller = controller or get_controller()
    return find_connection(controller.selection_connection_id)


def catalogs():
    global _catalogs
    if _catalogs is None:
        _catalogs = ModelCatalogs(state_directory())
    return _catalogs


def ready_connections():
    return [r for r in connection_records().values() if is_coding(r) and connection_ready(r)]


def generation_connections():
    """Credential-scoped Generative AI services, separate from coding chat."""
    return [r for r in connection_records().values() if not is_coding(r)]


def model_choices(controller):
    catalog = catalogs()
    catalog.seed(controller.state.get("connection_id"), controller.models)
    if controller._next_binding:
        catalog.seed(controller.selection_connection_id, controller.selection_models)
    choices = catalog.choices(ready_connections())
    return choices or controller.selection_models


def model_catalog_notice(controller):
    records = ready_connections()
    catalog = catalogs()
    loading = any(r["id"] in catalog.loading for r in records) or controller.models_loading
    errors = [r["name"] + ": " + catalog.errors[r["id"]] for r in records if r["id"] in catalog.errors]
    return loading, " · ".join(errors) or controller.models_error


def resolve_collaborator(connection_id):
    record = find_connection(connection_id)
    if not record or not is_coding(record) or not connection_ready(record) or not collaborators_allowed():
        raise RuntimeError("The requested collaborator connection is not available; open AI Providers")
    models = catalogs().entries.get(connection_id, [])
    if not models:
        raise RuntimeError("Refresh the collaborator's available models before delegating")
    return {"provider": record["mode"], "name": record["name"], "settings": connection_settings(record["mode"], record=record),
            "api_key": _memory_keys.get(record["id"], ""), "models": list(models)}


def refresh_connected_models(force=False):
    catalog = catalogs()
    for target in _controllers.values():
        catalog.seed(target.state.get("connection_id"), target.models)
    for record in ready_connections():
        catalog.refresh(record, connection_settings(record["mode"], record=record),
                        _memory_keys.get(record["id"], ""), force)


def choose_model_connection(controller, connection_id, model=""):
    record = find_connection(connection_id)
    if not record or not is_coding(record) or not connection_ready(record):
        raise RuntimeError(Text(n_("This connection is not available. Open AI Providers to reconnect.")))
    models = catalogs().entries.get(connection_id, [])
    if not models and connection_id == controller.state.get("connection_id"):
        models = controller.models
    controller.choose_connection(record["mode"], connection_settings(record["mode"], record=record),
        _memory_keys.get(record["id"], ""), connection_id, models, model)
    remember_preferred_mode(record["mode"])
    refresh_connected_models()


def connection_status(record):
    if is_team(record["mode"]):
        return team_account_ui.status(record)
    if record["mode"] in HTTP_MODES:
        result = _api_connections.results.get(record["id"])
        if result:
            return result.message
        if _memory_keys.get(record["id"]):
            return Text(n_("Key configured · not verified"))
        return saved_keys_ui.missing_key_status(record["id"]) or Text(n_("Enter API key to connect"))
    if record["mode"] == "claude_api":
        if _memory_keys.get(record["id"]):
            return Text(n_("API key configured"))
        return saved_keys_ui.missing_key_status(record["id"]) or Text(n_("Not connected"))
    identity = record["mode"] if record["reuse_native"] else record["id"]
    return _authentication.status.get(identity, Text(n_("Not connected")))


def connection_ready(record):
    if is_team(record["mode"]):  # Signed in with a seat, the provider offered and (coding) a grant key in memory.
        return team_account_ui.ready(record) and (not is_coding(record) or bool(_memory_keys.get(record["id"])))
    if record["mode"] in HTTP_MODES:
        result = _api_connections.results.get(record["id"])
        return bool(_memory_keys.get(record["id"]) and result and result.state == "verified" and result.models)
    if record["mode"] == "claude_api":
        return bool(_memory_keys.get(record["id"]))
    status = connection_status(record)
    return status.startswith("Connected to ") or status == "Native API credential configured"


def get_controller():
    global _controller, _sessions
    if _sessions is None:
        _sessions = SessionCollection(state_directory())
    if _controller is None or _controller.state["id"] != _sessions.active:
        _controller = _controllers.get(_sessions.active)
        if _controller is None:
            _controller = Controller(state_directory(), session_id=_sessions.active)
            from .external_host import external_label
            _controller.external_client = external_label(state_directory(), _sessions.active)
            _controller.set_document(document_key())
            from .memory_project_ui import schedule
            schedule(sys.modules[__name__], _controller)
            _controller.api_key = _memory_keys.get(_controller.state.get("connection_id"), _memory_keys.get(_controller.state["provider"], ""))
            _controllers[_sessions.active] = _controller
            _controller.delegation_resolver = resolve_collaborator
            _controller.checkpoint_factory = lambda target=_controller: capture_checkpoint(target)
            record = find_connection(_controller.state.get("connection_id"))
            if record and connection_ready(record):
                _controller.settings = connection_settings(record["mode"], record=record)
                _controller.refresh_models()
    return _controller


def capture_checkpoint(controller, *, purpose="request", _retain_native=False):
    global _capturing_checkpoint
    import threading
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Blender snapshots must be captured on the main thread")
    from . import native_programs
    is_staging = getattr(native_programs, "runtime_is_staging", native_programs.has_active_programs)
    if is_staging():
        raise RuntimeError(Text(n_("Finish the current native program or press Stop before starting another request; its isolated stage cannot be included in a request snapshot")))
    original = _recovery_original_path or bpy.data.filepath
    from .checkpoint_policy import clean_policy
    policy = clean_policy(getattr(controller, "_turn_checkpoint_policy", None)) if purpose == "request" else clean_policy()
    window = main_window()
    if not window:
        raise RuntimeError(Text(n_("No bound Blender window for the request snapshot")))
    # Private recovery files need the complete document, not a camera thumbnail.
    # Automatic thumbnails also create GPU contexts from a raw timer/approval
    # popup. Keep normal artist saves unchanged and restore the preference even
    # when native saving fails. This does not save application preferences.
    prefs = bpy.context.preferences
    filepaths = prefs.filepaths
    preview_type, save_images = filepaths.file_preview_type, filepaths.save_modified_images
    dirty = prefs.is_dirty
    dirty_editable = not prefs.bl_rna.properties['is_dirty'].is_readonly
    from .checkpoint_images import capture_packs
    autopack = bpy.data.use_autopack and capture_packs()
    try:
        _capturing_checkpoint = True
        # A capture never writes the person's image files nor packs external files into their
        # document (checkpoint_policy.rst, "Unsaved image pixels"); older builds pack unless held off.
        filepaths.file_preview_type, filepaths.save_modified_images = 'NONE', 'NEVER_SAVE'
        if autopack:
            bpy.data.use_autopack = False
        with bpy.context.temp_override(window=window):
            from .checkpoint_capture import capture_checkpoint as capture_native
            result = capture_native(controller.checkpoints, original, capture_mode=policy["capture_mode"],
                                    purpose=purpose, retain_native=_retain_native)
    finally:
        _capturing_checkpoint = False
        filepaths.file_preview_type, filepaths.save_modified_images = preview_type, save_images
        if autopack:
            bpy.data.use_autopack = True
        if dirty_editable:
            prefs.is_dirty = dirty
    record = result[0] if _retain_native else result
    # The full copy is already a complete snapshot. Deduplication runs later on
    # the snapshot worker and never delays or fails this capture.
    from . import snapshot_service
    snapshot_service.after_capture(controller, record)
    return result


def console_spaces():
    """Include inactive workspaces, cached spaces and native alternate inputs."""
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == "CONSOLE":
                    yield area, space


def remember_draft(controller):
    from .editor_types import read_chat_draft
    visible = [area.spaces.active for window in bpy.context.window_manager.windows
               for area in window.screen.areas if console.is_chat(area)]
    # Prefer visible editors, matching the existing multi-view policy. When all
    # chats are hidden, read their coding buffers without touching Python input.
    for space in visible or [s for _, s in console_spaces()]:
        draft = read_chat_draft(space)
        if draft is not None:
            _drafts[controller.state["id"]] = draft


def show_session(session_id):
    from .editor_types import require_chat_draft_access, write_chat_draft
    require_chat_draft_access()
    # Collection.create already records the new active ID; the displayed view
    # still belongs to the previous controller until this transition finishes.
    old = _controller or get_controller()
    from .tool_finalization import save_observed_state
    remember_draft(old)
    save_observed_state(old, leave=True)  # The chat left is one base line again, readable by any build.
    _sessions.update(old.state)
    _sessions.activate(session_id)
    controller = get_controller()
    remember_preferred_mode(controller.state["provider"])
    draft = _drafts.get(session_id, ("", 0))
    for area, space in console_spaces():
        write_chat_draft(space, draft)
        console._versions.pop(area.as_pointer(), None)
        area.tag_redraw()
    return controller


def create_session(mode=None, connection_id=None):
    from .editor_types import require_chat_draft_access
    require_chat_draft_access()
    current = get_controller()
    # New chats inherit a conversation's choice, not a globally exclusive account.
    identity = connection_id or (current.selection_connection_id if mode is None else None)
    record = find_connection(identity)
    if mode is None and not connection_id and (not record or not connection_ready(record)):
        record = next(iter(ready_connections()), None)
    if record:
        mode, connection_id = record["mode"], record["id"]
    mode = mode or (preferences().provider if preferences() else current.state["provider"])
    identity = _sessions.create(mode)
    controller = show_session(identity)
    controller.state["connection_id"] = connection_id
    controller.api_key = _memory_keys.get(connection_id, "")
    controller.save()
    return controller


def connection_settings(mode, prefs=None, record=None):
    prefs = prefs or preferences()
    settings = {
        "codex_path": prefs.codex_path if prefs else "",
        "claude_path": prefs.claude_path if prefs else "",
        "model": (prefs.claude_model if mode.startswith("claude_") else prefs.codex_model) if prefs and mode not in CUSTOM_PROVIDER_MODES else "",
        "reuse_codex_login": prefs.reuse_codex_login if prefs else True,
        "reasoning": prefs.reasoning if prefs else "low",
        "auth_directory": connection_directory(),
    }
    if record:
        settings["reuse_codex_login"] = record["reuse_native"]
        settings["connection_id"] = None if record["reuse_native"] else record["id"]
        settings["model"] = record.get("model") or ""
        settings["native_profile"] = record.get("native_profile")
    return settings


def connection_available():
    controller = get_controller()
    mode = controller.selection_provider
    pending = controller.selection_connection_id != controller.state.get("connection_id")
    if controller.connection_error and not pending:
        return False
    if controller.selection_connection_id and not connection_record(controller):
        return False
    if controller.provider is not None and not pending:
        return True
    record = connection_record(controller) or native_suggestions.adoptable_connection(connections(), mode)
    if record:
        return connection_ready(record)
    # A detected default sign-in is only a suggestion: a chat without a
    # connection never runs on it.
    return mode in native_suggestions.LEGACY_KEY_MODES and bool(_memory_keys.get(mode))


def prepare_controller():
    prefs = preferences()
    controller = get_controller()
    controller.set_document(document_key())
    from .memory_project_ui import bind_current
    bind_current(sys.modules[__name__], controller)
    controller.apply_next_connection()
    mode = prefs.provider if prefs else "codex_subscription"
    record = connection_record(controller)
    if controller.selection_connection_id and record is None:
        raise RuntimeError(Text(n_("The saved connection is unavailable. Open AI Providers to choose a connection.")))
    if record:
        mode = record["mode"]
    else:
        active = native_suggestions.adoptable_connection(connections(), mode)
        if active:
            record = active
            # Legacy unbound chats acquire a connection without losing history.
            controller.remember_runtime()
            previous = controller.state["runtime_sessions"].pop(mode, None)
            if previous:
                controller.state["runtime_sessions"][active["id"]] = previous
        elif not (mode in native_suggestions.LEGACY_KEY_MODES and _memory_keys.get(mode)):
            # Never fall back to the computer's default native sign-in; the
            # person adds a connection first.
            if not ready_connections():
                controller.setup_required = True
            raise RuntimeError(Text(n_("This chat has no connection. Connect Claude or ChatGPT in the chat, "
                                       "or choose a model from a connected account.")))
    controller.context_rebase_tokens = int(getattr(prefs, "context_rebase_tokens", 0) or 0) if prefs else 0
    controller.helper_consent = bool(getattr(prefs, "helper_standing_consent", False)) if prefs else False
    controller.select(mode, connection_settings(mode, prefs, record),
                      _memory_keys.get(record["id"] if record else mode, ""),
                      record["id"] if record else None,
                      catalogs().entries.get(record["id"]) if record else None)
    from .files import FileWorkspace
    project_path = _recovery_original_path or bpy.data.filepath
    controller.files = FileWorkspace(Path(project_path).parent if project_path else controller.workspace,
                                    excluded=[state_directory(), connection_directory()])
    refresh_connected_models()
    controller.collaborators = [{"connection_id": r["id"], "name": r["name"], "provider": r["mode"],
        "billing": {"API billing": "API account"}.get(billing_label(r["mode"]), billing_label(r["mode"])),
        "models": {m["id"]: m["name"] for m in catalogs().entries.get(r["id"], [])}}  # {id: name}: compact JSON (context_ledger).
        for r in ready_connections() if catalogs().entries.get(r["id"]) and collaborators_allowed()]


def draw_ai_providers(layout, context):
    from . import providers_page_host  # The canvas page (providers_page_*.py); the rest is its fallback.
    if providers_page_host.draw_panel(layout, context):
        return
    prefs, header = preferences(), layout.row()
    header.label(text="AI Providers")
    team_account_ui.draw_new_connection(header)
    if prefs is None:
        layout.operator("preferences.addon_enable", text="Enable Agent Chat").module = "angelo_agent"
        return
    from . import data_migration, state_migration
    for notice in (data_migration.attention_message(), state_migration.attention_message()):
        if notice:
            (box := layout.box()).label(text="A pre-rename folder needs attention", icon='ERROR')
            for line in text_layout.label_rows(iface_(notice)):
                box.label(text=line, translate=False)
    store = connections()
    for problem in saved_keys_ui.notices():
        layout.label(text=shown(problem), icon='ERROR', translate=False)
    if agent_runtime.override() is not None:  # Which Codex runs, when the environment chose it (agent_runtime).
        layout.label(text=iface_("Agent runtime override: ANGELO_AGENT_RUNTIME={value}", value=agent_runtime.override()), icon='INFO', translate=False)
    personal = team_account_ui.draw(layout, sys.modules[__name__])  # False: only team connections may be used now.
    for group, title, description in GROUPS if personal else ():
        layout.separator()
        (row := layout.row()).label(text=iface_(title), translate=False)
        row.operator("angelo.new_connection", text="Add connection", icon='ADD').group = group
        layout.label(text="All connected accounts are options in chat and as collaborators." if group == CODING else "Bring your own API keys. Generate through chat or the Generative AI editor.")
        if group == CODING and prefs is not None:
            layout.prop(prefs, "helper_standing_consent")
        if group == CODING:
            draw_native_suggestions(layout, store)
        records = [r for r in store.records.values() if spec_for(r["mode"])["group"] == group]
        if not records:
            layout.label(text="No connections yet.")
        for record in records:
            box = layout.box()
            row = box.row()
            row.label(text=record["name"], icon='USER' if record["method"] == "SUBSCRIPTION" else 'KEY_HLT')
            if connection_ready(record):
                row.label(text="Available in chat" if group == CODING else "Key verified", icon='CHECKMARK')
            edit = row.operator("angelo.new_connection", text="", icon='GREASEPENCIL')
            edit.connection_id, edit.group = record["id"], group
            row.operator("angelo.connection_menu", text="", icon='DOWNARROW_HLT').connection_id = record["id"]
            method = n_("Subscription") if record["method"] == "SUBSCRIPTION" else n_("API key")
            box.label(text=iface_("{provider} · {method} · {status}", provider=PROVIDER_NAMES[record["provider"]],
                                  method=iface_(method), status=shown(connection_status(record))), translate=False)
            terms = plan_terms(record["mode"])
            if terms:
                box.label(text=terms["note"], icon='INFO')
            storage = saved_keys_ui.storage_label(record, bool(_memory_keys.get(record["id"])))
            if storage:
                box.label(text=shown(storage), translate=False)
            result = _api_connections.results.get(record["id"])
            if result and result.balance is not None:
                box.label(text=iface_("API credits available: {balance}", balance=format(result.balance, "g")), translate=False)
    layout.separator()
    draw_always_allow(layout, prefs)  # Outside the connection groups: a team-only organization still sees it.
    from . import studio
    studio.draw_enhancer_settings(layout)
    layout.separator()
    from . import external_host
    external_host.draw_preferences(layout, prefs)


def draw_native_suggestions(layout, store):
    """Offer each detected default sign-in. The person confirms the prefilled form."""
    for suggestion in native_suggestions.suggestions(store, _authentication.status):
        box = layout.box()
        box.label(text=suggestion["title"], icon='INFO')
        box.label(text=native_suggestions.SUGGESTION_NOTE)
        row = box.row()
        form = row.operator("angelo.new_connection", text="Add connection", icon='ADD')
        for name, value in native_suggestions.form_properties(suggestion["mode"]).items():
            setattr(form, name, value)
        row.operator("angelo.dismiss_native_suggestion", text="Dismiss").mode = suggestion["mode"]


class ANGELO_Preferences(AddonPreferences):
    bl_idname = __package__
    provider: EnumProperty(name="Connection", items=PROVIDERS, default="codex_subscription")
    codex_model: StringProperty(name="Codex model", default="")
    claude_model: StringProperty(name="Claude model", default="")
    reasoning: EnumProperty(name="Reasoning", items=[("low", "Low — responsive edits", ""), ("medium", "Medium", ""), ("high", "High — complex planning", "")], default="low")
    codex_path: StringProperty(name="Codex executable", subtype="FILE_PATH")
    claude_path: StringProperty(name="Claude Code executable", subtype="FILE_PATH")
    reuse_codex_login: BoolProperty(name="Use existing native Codex sign-in", default=True,
                                   description="Use your CLI's existing login; configuration overrides are process-local. API-key mode always uses its own profile")
    allow_local_cli: BoolProperty(name="Allow local CLI access", default=False,
                                  description="Let coding agents on this computer, running as you, observe the open document "
                                              "through the read-only `angelo` CLI. Off by default; turning it off closes the socket")
    # Standing helper consent (doc/angelo/model_routing.rst): drawn in
    # AI Providers and the add-on preferences so it can be turned off.
    helper_standing_consent: BoolProperty(
        name="Run fast helper models without asking", default=True,
        description="Let the agent consult a fast model (for example Claude Haiku) on the same connection "
                    "without an approval click. Each consultation is still shown in the chat and recorded with "
                    "its usage; other accounts or providers always ask")
    # Always allow scripts: a view of the choice always_allow.py saves; only the person changes it (the setter's guard).
    always_allow_scripts: BoolProperty(
        name="Always allow scripts", get=always_allow_get, set=always_allow_set,
        description="In chats set to Auto, run the agent's Python scripts without asking. Scripts have full access to "
                    "files, the network and other apps on this computer. Ask before changes still asks; Read only still "
                    "refuses")
    # Voice is hidden by default until it can be tested properly; this opt-in shows the microphone again (voice_ui.py).
    experimental_voice: BoolProperty(name="Experimental voice", default=False,
                                     description="Show the microphone in the chat. Needs macOS, a ChatGPT (Codex) connection "
                                                 "and ffmpeg/ffplay; not yet tested with real microphone audio")
    # Off by default pending a product decision on its presentation (see
    # context_rebase). Not drawn in the preferences UI yet.
    context_rebase_tokens: IntProperty(name="Refresh Claude context at (tokens)", default=0, min=0, max=1000000,
                                       description="When a Claude chat's measured prompt reaches this size, its next message "
                                                   "starts a fresh model session seeded from the chat's task memory, requests, "
                                                   "recent results and open previews. 0 turns this off")

    def draw(self, context):
        self.layout.label(text="Connections are in Preferences > AI Providers.")
        self.layout.prop(self, "helper_standing_consent")
        draw_always_allow(self.layout, self)
        self.layout.prop(self, "experimental_voice")
        self.layout.operator("angelo.settings", text="Open AI Providers", icon='PREFERENCES')


_connection_provider_items = {group: provider_items(group) for group, _, _ in GROUPS}


def new_connection_providers(self, context):
    return _connection_provider_items[self.group]


def new_connection_group_changed(self, context):
    self.provider = _connection_provider_items[self.group][0][0]


def new_connection_provider_changed(self, context):
    supported = methods(self.group, self.provider)
    if supported and self.method not in supported:
        self.method = supported[0]


def connection_in_use(identity):
    from . import studio
    return studio.connection_in_use(identity) or any((c.busy and c.state.get("connection_id") == identity)
               or any(job.connection_id == identity for job in c.delegate_jobs.values()) for c in _controllers.values())


def replace_connection_key(record, secret):
    identity = record["id"]
    if connection_in_use(identity):
        raise RuntimeError(Text(n_("Stop this connection's active turns before replacing its API key")))
    secret = clean_key(secret)
    for controller in _controllers.values():
        controller.invalidate_runtime(identity)
        if controller.state.get("connection_id") == identity:
            if controller.provider:
                import threading
                threading.Thread(target=controller.provider.close, daemon=True).start()
            controller.provider = None
            controller.api_key = secret
            controller.models = []
    catalogs().invalidate(identity)
    _api_connections.invalidate(identity)
    _memory_keys[identity] = secret


def remember_saved_key(record, remember, new_key, existing):
    """Apply the form's Remember choice (Keychain, Credential Manager or keyring) to the key now in memory.

    A new key is saved (created or replaced) when remembered; otherwise an
    existing connection's saved key is deleted so an older key cannot return
    after a restart. With no new key, the choice applies to the current key.
    """
    identity, stored = record["id"], saved_keys_ui.is_remembered(record["id"])
    if remember and _memory_keys.get(identity) and (new_key or stored is not True):
        saved_keys_ui.remember(record, _memory_keys[identity])
    elif not remember and existing and (new_key or stored is not False):
        saved_keys_ui.forget(identity)
    elif not remember:
        saved_keys_ui.mark_memory_only(identity)


class ANGELO_OT_new_connection(Operator):
    bl_idname = "angelo.new_connection"
    bl_label = "New AI Connection"
    connection_id: StringProperty(options={'SKIP_SAVE'})
    group: EnumProperty(name="Connection group", items=GROUPS, default=CODING, update=new_connection_group_changed)
    provider: EnumProperty(name="Provider", items=new_connection_providers, update=new_connection_provider_changed)
    method: EnumProperty(name="Connect with", items=[("SUBSCRIPTION", "Subscription", "Sign in through the provider"),
                                                       ("API_KEY", "API key", "Use your own API key")], default="SUBSCRIPTION")
    connection_name: StringProperty(name="Name", description="Optional name for this connection", options={'SKIP_SAVE'})
    api_key: StringProperty(name="API key", subtype='PASSWORD', options={'SKIP_SAVE'})
    remember_key: BoolProperty(name=saved_keys_ui.TEXT.remember, default=True, options={'SKIP_SAVE'},
                               description=saved_keys_ui.TEXT.remember_description)
    advanced: BoolProperty(name="Advanced", default=False, options={'SKIP_SAVE'})
    model: StringProperty(name="Model", options={'SKIP_SAVE'})
    runtime_path: StringProperty(name="Executable", subtype='FILE_PATH', options={'SKIP_SAVE'})
    reuse_existing: BoolProperty(name="Use existing sign-in on this computer", default=True, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        record = connections().records.get(self.connection_id)
        if record:
            self.group = spec_for(record["mode"])["group"]
            self.provider, self.method = record["provider"], record["method"]
            self.connection_name, self.model = record["name"], record.get("model") or ""
            self.remember_key = saved_keys_ui.is_remembered(record["id"]) is not False
        return context.window_manager.invoke_props_dialog(self, width=480,
                                                        title=iface_("Edit connection") if record else iface_("New connection"), confirm_text=iface_("Save") if record else iface_("Connect"))

    def cancel(self, context):
        self.api_key = ""

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        identity = layout.column()
        identity.enabled = not bool(self.connection_id)
        identity.prop(self, "group")
        identity.prop(self, "provider")
        supported = methods(self.group, self.provider)
        if len(supported) > 1:
            identity.prop(self, "method")
        else:
            identity.label(text="Connect with your own API key")
        layout.prop(self, "connection_name", text="Name (optional)")
        method = self.method if len(supported) > 1 else "API_KEY"
        if method == "API_KEY":
            layout.prop(self, "api_key", text="New API key" if self.connection_id else "API key")
            if self.connection_id:
                layout.label(text="Leave the key blank to keep the current credential.")
            else:
                layout.label(text="API usage is billed to your API account.")
            mode = MODE_BY_CONNECTION[(self.group, self.provider, method)]
            if mode == "codex_api":
                layout.label(text="The Codex CLI stores this key in its own OS keyring.")
            elif saved_keys_ui.available():
                layout.prop(self, "remember_key")
                layout.label(text=iface_("On: saved in {location} for this profile.", location=shown(saved_keys_ui.store_text().location)), translate=False)
                layout.label(text="Off: memory only; enter it again after restarting.")
            else:
                layout.label(text=shown(saved_keys_ui.unavailable_reason()), translate=False)
            if mode in HTTP_MODES:
                layout.label(text="Connection checks never generate content or consume inference tokens.")
            if mode == "seedream_api":
                layout.label(text="BytePlus Singapore key. Verification is deferred until generation.")
        else:
            service = "ChatGPT" if self.provider == "OPENAI" else "Claude"
            mode = "codex_subscription" if self.provider == "OPENAI" else "claude_subscription"
            if not self.connection_id and _authentication.status.get(mode, "").startswith("Connected to "):
                layout.prop(self, "reuse_existing")
            layout.label(text=(iface_("Connect uses your existing native sign-in.") if self.reuse_existing and _authentication.status.get(mode, "").startswith("Connected to ") else iface_("Connect opens {service} sign-in in your browser.", service=service)) if not self.connection_id else iface_("Your subscription sign-in stays with its official runtime."), translate=False)
        if self.group == CODING:
            layout.separator()
            layout.prop(self, "advanced", text="Advanced", emboss=False, icon='TRIA_DOWN' if self.advanced else 'TRIA_RIGHT')
        if self.advanced and self.group == CODING:
            layout.prop(self, "model")
            layout.label(text="Blank uses the runtime's default model.")
            if not (runtime_kind(mode) == "codex" and agent_runtime.present()):
                layout.prop(self, "runtime_path")  # Angelo's agent runtime runs every Codex connection.

    def execute(self, context):
        secret, self.api_key = self.api_key.strip(), ""
        record = connections().records.get(self.connection_id)
        if self.connection_id and not record:
            self.report({'ERROR'}, rpt_("This connection was removed; open a new connection form"))
            return {'CANCELLED'}
        if record:
            self.group, self.provider, self.method = spec_for(record["mode"])["group"], record["provider"], record["method"]
        supported = methods(self.group, self.provider)
        if not supported:
            self.report({'ERROR'}, rpt_("Choose a provider available in this connection group"))
            return {'CANCELLED'}
        self.method = self.method if self.method in supported else supported[0]
        if self.method == "API_KEY" and not record and not secret:
            self.report({'ERROR'}, rpt_("Enter an API key to create this connection"))
            return {'CANCELLED'}
        if secret:
            try:
                secret = clean_key(secret)
                if record and connection_in_use(record["id"]):
                    raise RuntimeError(Text(n_("Stop this connection's active turns before replacing its API key")))
            except (ValueError, RuntimeError) as error:
                self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
                return {'CANCELLED'}
        if record:
            record["name"] = self.connection_name.strip()[:64] or record["name"]
            record["model"] = self.model.strip()
            connections().save()
        else:
            native_mode = "codex_subscription" if self.provider == "OPENAI" else "claude_subscription"
            reuse = self.method == "SUBSCRIPTION" and self.reuse_existing and _authentication.status.get(native_mode, "").startswith("Connected to ")
            record = connections().add(self.provider, self.method, self.connection_name, self.model, reuse_native=reuse, group=self.group)
        if self.runtime_path and preferences() and is_coding(record):
            setattr(preferences(), "codex_path" if runtime_kind(record["mode"]) == "codex" else "claude_path", self.runtime_path)
        mode = record["mode"]
        try:
            if self.method == "API_KEY":
                if secret:
                    replace_connection_key(record, secret)
                if saved_keys_ui.uses_saved_key(record):
                    remember_saved_key(record, self.remember_key, bool(secret), bool(self.connection_id))
                if mode in HTTP_MODES and _memory_keys.get(record["id"]):
                    _api_connections.start(record, _memory_keys[record["id"]])
                if secret and mode == "codex_api":
                    _authentication.start(mode, connection_settings(mode, record=record), state_directory(),
                                          login=True, identity=record["id"], api_key=secret)
            elif not self.connection_id and not record["reuse_native"]:
                _authentication.start(mode, connection_settings(mode, record=record), state_directory(), login=True, identity=record["id"])
        except Exception as error:
            _authentication.status[record["id"]] = Text(n_("Not connected"))
            self.report({'ERROR'}, str(error))
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_connection_use(Operator):
    bl_idname = "angelo.connection_use"
    bl_label = "Use Connection for This Chat"
    connection_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            choose_model_connection(get_controller(), self.connection_id)
            preferences_ui.redraw()
        except (RuntimeError, ValueError) as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_connection_menu(Operator):
    bl_idname = "angelo.connection_menu"
    bl_label = "Connection Actions"
    connection_id: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        record = connections().records.get(self.connection_id)
        if not record:
            return {'CANCELLED'}
        def draw(menu, context):
            identity = record["mode"] if record["reuse_native"] else record["id"]
            if identity in _authentication.running:
                menu.layout.operator("angelo.cancel_signin", text="Cancel connection").mode = identity
            elif record["method"] == "SUBSCRIPTION":
                menu.layout.operator("angelo.connection_signin", text="Reconnect").connection_id = record["id"]
            if saved_keys_ui.can_load(record, bool(_memory_keys.get(record["id"]))):
                menu.layout.operator("angelo.connection_load_key",
                                     text=iface_(saved_keys_ui.store_text().load_label), translate=False).connection_id = record["id"]
            menu.layout.operator("angelo.connection_check", text="Refresh status").connection_id = record["id"]
            menu.layout.separator()
            menu.layout.operator("angelo.connection_remove", text="Remove connection", icon='X').connection_id = record["id"]
        context.window_manager.popup_menu(draw, title=record["name"])
        return {'FINISHED'}


class ANGELO_OT_connection_check(Operator):
    bl_idname = "angelo.connection_check"
    bl_label = "Check Connection"
    connection_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        record = connections().records.get(self.connection_id)
        if not record:
            return {'CANCELLED'}
        if record["mode"] in HTTP_MODES:
            try:
                _api_connections.start(record, _memory_keys.get(record["id"], ""))
            except (RuntimeError, ValueError) as error:
                self.report({'WARNING'}, str(error))
            preferences_ui.redraw()
            return {'FINISHED'}
        if record["mode"] == "claude_api":
            preferences_ui.redraw()
            return {'FINISHED'}
        identity = record["mode"] if record["reuse_native"] else record["id"]
        try:
            _authentication.start(record["mode"], connection_settings(record["mode"], record=record), state_directory(), identity=identity)
        except RuntimeError as error:
            self.report({'WARNING'}, str(error))
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_connection_load_key(Operator):
    bl_idname = "angelo.connection_load_key"
    bl_label = saved_keys_ui.TEXT.load_label
    bl_description = saved_keys_ui.TEXT.load_description
    connection_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        record = connections().records.get(self.connection_id)
        if not record:
            return {'CANCELLED'}
        try:
            # The one saved-key call allowed to show an OS prompt: the person asked for it.
            saved_keys_ui.load(record, interactive=True)
        except (RuntimeError, ValueError) as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
            return {'CANCELLED'}
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_connection_signin(Operator):
    bl_idname = "angelo.connection_signin"
    bl_label = "Reconnect Subscription"
    connection_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        record = connections().records.get(self.connection_id)
        if not record or record["method"] != "SUBSCRIPTION" or any((c.busy and c.state.get("connection_id") == self.connection_id)
                or any(job.connection_id == self.connection_id for job in c.delegate_jobs.values()) for c in _controllers.values()):
            self.report({'ERROR'}, rpt_("Stop this connection's active turns before reconnecting"))
            return {'CANCELLED'}
        identity = record["mode"] if record["reuse_native"] else record["id"]
        try:
            _authentication.start(record["mode"], connection_settings(record["mode"], record=record), state_directory(), login=True, identity=identity)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_dismiss_native_suggestion(Operator):
    bl_idname = "angelo.dismiss_native_suggestion"
    bl_label = "Dismiss Suggested Sign-In"
    bl_description = ("Stop suggesting this sign-in. You can still add it with Add connection; "
                      "the provider account stays signed in")
    mode: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if self.mode not in native_suggestions.SUGGESTIONS:
            self.report({'ERROR'}, rpt_("Only a suggested native sign-in can be dismissed"))
            return {'CANCELLED'}
        connections().dismiss_native(self.mode)
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_connection_remove(Operator):
    bl_idname = "angelo.connection_remove"
    bl_label = "Remove Connection"
    bl_description = saved_keys_ui.TEXT.remove_description
    connection_id: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        record = connections().records.get(self.connection_id)
        if record:
            _authentication.cancel(record["mode"] if record["reuse_native"] else record["id"])
        _api_connections.invalidate(self.connection_id)
        for controller in _controllers.values():
            controller.invalidate_runtime(self.connection_id)
            if controller.state.get("connection_id") == self.connection_id:
                controller.stop()
                if controller.provider:
                    import threading
                    threading.Thread(target=controller.provider.close, daemon=True).start()
                controller.provider = None
                controller.api_key = ""
                controller.connection_error = True
        _memory_keys.pop(self.connection_id, None)
        if record and saved_keys_ui.uses_saved_key(record):
            saved_keys_ui.forget(self.connection_id)
        catalogs().invalidate(self.connection_id)
        connections().remove(self.connection_id)
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_settings(Operator):
    bl_idname = "angelo.settings"
    bl_label = "Angelo Connection Settings"
    bl_description = "Connection setup lives here, not in the chat editor"

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        try:
            preferences_ui.select()
            bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
            preferences_ui.redraw()
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_api_key(Operator):
    bl_idname = "angelo.api_key"
    bl_label = "API Key"
    key: StringProperty(name="API key", subtype="PASSWORD", options={'SKIP_SAVE'})
    mode: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=500)

    def draw(self, context):
        self.layout.prop(self, "key")
        self.layout.label(text="Never paste keys into chat. This field is not saved in a blend file.")

    def execute(self, context):
        prefs = preferences()
        mode = self.mode or (prefs.provider if prefs else "")
        if not prefs or mode not in {"codex_api", "claude_api"} or get_controller().busy:
            self.key = ""
            self.report({'ERROR'}, rpt_("Select an API connection in Settings, with no active turn"))
            return {'CANCELLED'}
        previous = _memory_keys.get(mode, "")
        _memory_keys[mode] = self.key.strip()
        self.key = ""
        if mode == prefs.provider:
            if previous != _memory_keys[mode]:
                get_controller().invalidate_runtime()
            prepare_controller()
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_copy_login(Operator):
    bl_idname = "angelo.copy_login"
    bl_label = "Copy Native Sign-In Command"
    bl_description = "Use the provider's own CLI/browser login; no credentials are collected by this button"
    mode: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        prefs = preferences()
        if not prefs:
            return {'CANCELLED'}
        try:
            mode = self.mode or prefs.provider
            if mode not in {"codex_subscription", "claude_subscription"}:
                self.report({'INFO'}, rpt_("Use Enter API Key for the selected API connection"))
                return {'CANCELLED'}
            arguments, env = native_auth_command(mode, connection_settings(mode, prefs), get_controller().store.directory, login=True)
            command = shlex.join(arguments)
            if mode == "codex_subscription" and not prefs.reuse_codex_login:
                command = "CODEX_HOME=" + shlex.quote(env["CODEX_HOME"]) + " " + command  # i18n: shell command copied to the clipboard
            context.window_manager.clipboard = command
            self.report({'INFO'}, rpt_("Native sign-in command copied. Run it in Terminal, then return to chat."))
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_use_connection(Operator):
    bl_idname = "angelo.use_connection"
    bl_label = "Use AI Connection"
    mode: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        prefs = preferences()
        if not prefs or get_controller().busy:
            self.report({'ERROR'}, rpt_("Stop the current turn before changing its connection"))
            return {'CANCELLED'}
        prefs.provider = self.mode
        prepare_controller()
        preferences_ui.redraw()
        return {'FINISHED'}


class ANGELO_OT_native_signin(Operator):
    bl_idname = "angelo.native_signin"
    bl_label = "Native Subscription Sign-In"
    mode: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if any(c.busy for c in _controllers.values()):
            self.report({'ERROR'}, rpt_("Stop active turns before signing in"))
            return {'CANCELLED'}
        try:
            _authentication.start(self.mode, connection_settings(self.mode), get_controller().store.directory, login=True)
            preferences_ui.redraw()
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_check_signin(Operator):
    bl_idname = "angelo.check_signin"
    bl_label = "Check Native Sign-In"
    mode: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            _authentication.start(self.mode, connection_settings(self.mode), get_controller().store.directory)
            preferences_ui.redraw()
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_cancel_signin(Operator):
    bl_idname = "angelo.cancel_signin"
    bl_label = "Cancel Native Sign-In"
    mode: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        _authentication.cancel(self.mode)
        return {'FINISHED'}


def setup_chat_area(window, area):
    from .editor_types import select_console
    select_console(window, area, coding=True)
    area.spaces.active.font_size = 18
    area.spaces.active.prompt = "Ask > "
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    if region:
        with bpy.context.temp_override(window=window, area=area, region=region):
            if not area.spaces.active.history:
                bpy.ops.console.history_append(text="", current_character=0, remove_duplicates=True)
            console.render(bpy.context, force=True)
            chat_view.ensure_input(window, area)


def request_chat_dock(window, side, *, draft_override=None, on_complete=None, on_error=None):
    """Defer layout changes so a menu handler never holds a deleted region."""
    global _main_window_pointer, _layout_timer
    from .input_policy import interaction_active
    if interaction_active(window):
        raise RuntimeError(Text(n_("Finish or cancel the active Blender tool before moving Coding Agent")))
    if _layout_timer is not None:
        raise RuntimeError(Text(n_("The chat dock is already moving")))
    if not any(a.type == "VIEW_3D" for a in window.screen.areas):
        raise RuntimeError(Text(n_("Keep a 3D Viewport open before docking chat")))
    _main_window_pointer = window.as_pointer()
    existing = next((a for a in window.screen.areas if console.is_chat(a)), None)
    pointer = existing.as_pointer() if existing else None
    space = existing.spaces.active if existing else None
    draft = space.history[-1].body if space and space.history else ""
    cursor = space.history[-1].current_character if space and space.history else 0
    font = space.font_size if space else 18
    controller = get_controller()
    session_id = controller.state["id"]
    if existing:
        rects = docking.rectangles(window)
        if not any(docking.is_edge_dock(rects, pointer, edge) for edge in ("LEFT", "RIGHT")):
            # A native editor choice can occupy just the Properties/Outliner
            # slot, not a full-height dock. Restore its cached editor instead of
            # attempting destructive joins against mismatched neighboring rows.
            from .editor_types import restore_previous_editor
            restore_previous_editor(window, existing)
            _dock_metadata.pop(pointer, None)
            pointer = None
    job = docking.relocate_chat(window, side, pointer, _dock_metadata.get(pointer, False))
    window_pointer = window.as_pointer()

    def failed(error):
        global _layout_timer
        _layout_timer = None
        if on_error:
            on_error(error)
        else:
            controller.emit("message", {"role": "error", **notice("Chat layout: {error}", error=str(error))})

    def advance():
        global _layout_timer, _dock_area_pointer, _dock_side
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == window_pointer), None)
        if target is None:
            failed(RuntimeError("The target Blender window closed while chat was moving"))  # i18n: English detail inside the stored Chat layout notice
            return None
        if draft_override is not None and get_controller().state["id"] != session_id:
            failed(RuntimeError("The conversation changed before the draft could return to its dock"))  # i18n: English detail inside the stored Chat layout notice
            return None
        if interaction_active(target):
            return .1
        try:
            return next(job)
        except StopIteration as finished:
            try:
                area_pointer, expanded = finished.value
                area = docking.area_for(target, area_pointer)
                active = get_controller()
                if draft_override is not None and active.state["id"] != session_id:
                    raise RuntimeError("The conversation changed before the draft could return to its dock")
                if draft_override is not None:
                    value, caret, size = draft_override() if callable(draft_override) else draft_override
                elif active.state["id"] == session_id:
                    value, caret, size = draft, cursor, font
                else:
                    value, caret = _drafts.get(active.state["id"], ("", 0))
                    size = font
                active.set_document(document_key())
                setup_chat_area(target, area)
                area.spaces.active.font_size = size
                area.spaces.active.history[-1].body = value
                area.spaces.active.history[-1].current_character = caret
                _drafts[active.state["id"]] = (value, caret)
                _dock_area_pointer, _dock_side = area_pointer, side
                if pointer and pointer != area_pointer:
                    _dock_metadata.pop(pointer, None)
                _dock_metadata[area_pointer] = expanded
                _layout_timer = None
                if on_complete:
                    on_complete(target, area)
            except Exception as error:
                failed(error)
        except Exception as error:
            failed(error)
        return None

    _layout_timer = advance
    bpy.app.timers.register(advance, first_interval=0.05)


class ANGELO_OT_open_chat(Operator):
    bl_idname = "angelo.open_chat"
    bl_label = "Angelo Chat"
    bl_description = "Open a separate editor area; never overlay the viewport"
    side: EnumProperty(items=[("RIGHT", "Right", ""), ("LEFT", "Left", "")], default="RIGHT")

    def execute(self, context):
        try:
            request_chat_dock(context.window, self.side)
        except RuntimeError as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_dock_side(Operator):
    bl_idname = "angelo.dock_side"
    bl_label = "Move Chat"
    side: EnumProperty(items=[("RIGHT", "Right", ""), ("LEFT", "Left", "")], default="RIGHT")

    def execute(self, context):
        if not console.is_chat(context.area):
            return {'CANCELLED'}
        if context.window != main_window():
            self.report({'INFO'}, rpt_("Return chat to its dock before changing sides"))
            return {'CANCELLED'}
        try:
            request_chat_dock(context.window, self.side)
        except RuntimeError as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_pop_out(Operator):
    bl_idname = "angelo.pop_out"
    bl_label = "Pop Out Chat"
    bl_description = "Move chat into a normal movable, resizable operating-system window"

    def execute(self, context):
        global _dock_area_pointer
        if not console.is_chat(context.area):
            return {'CANCELLED'}
        original_window = context.window
        original = context.area
        space = original.spaces.active
        draft = space.history[-1].body if space.history else ""
        cursor = space.history[-1].current_character if space.history else 0
        font_size = space.font_size
        before = {w.as_pointer() for w in context.window_manager.windows}
        bpy.ops.screen.area_dupli('INVOKE_DEFAULT')
        new_window = next((w for w in context.window_manager.windows if w.as_pointer() not in before), None)
        if new_window is None:
            return {'CANCELLED'}
        if original_window == main_window():
            _dock_area_pointer = original.as_pointer()
        from .editor_types import restore_previous_editor
        restore_previous_editor(original_window, original)
        for area in new_window.screen.areas:
            if area.type == "CONSOLE":
                setup_chat_area(new_window, area)
                area.spaces.active.font_size = font_size
                area.spaces.active.history[-1].body = draft
                area.spaces.active.history[-1].current_character = cursor
        return {'FINISHED'}


class ANGELO_OT_return_dock(Operator):
    bl_idname = "angelo.return_dock"
    bl_label = "Return Chat to Dock"

    def execute(self, context):
        target = main_window()
        if target is None or target == context.window or not console.is_chat(context.area):
            return {'CANCELLED'}
        old_pointer = context.window.as_pointer()
        space_pointer = context.space_data.as_pointer()
        controller = get_controller()
        source = context.space_data
        draft = (source.history[-1].body, source.history[-1].current_character) if source.history else ("", 0)
        font = source.font_size
        _drafts[controller.state["id"]] = draft

        def current_draft():
            from .editor_types import read_chat_draft
            old = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == old_pointer), None)
            if old:
                for item in old.screen.areas:
                    for space in item.spaces:
                        if space.type == "CONSOLE" and space.as_pointer() == space_pointer:
                            value = read_chat_draft(space)
                            if value is not None:
                                size = space.font_size if space.language == console.language_id else font
                                return *value, size
                raise RuntimeError("The pop-out editor changed before its draft could be transferred")
            return *draft, font

        def close_source(_window, _area):
            old = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == old_pointer), None)
            if old and len(old.screen.areas) == 1 and console.is_chat(old.screen.areas[0]):
                with bpy.context.temp_override(window=old):
                    bpy.ops.wm.window_close()

        def failed(error):
            get_controller().emit("message", {"role": "error", **notice("Return to dock did not complete; the pop-out was kept open. {error}", error=str(error))})

        area = next((a for a in target.screen.areas if a.as_pointer() == _dock_area_pointer), None)
        if area:
            setup_chat_area(target, area)
            value, caret, size = current_draft()
            area.spaces.active.history[-1].body = value
            area.spaces.active.history[-1].current_character = caret
            area.spaces.active.font_size = size
            _drafts[controller.state["id"]] = (value, caret)
            close_source(target, area)
        else:
            try:
                request_chat_dock(target, _dock_side, draft_override=current_draft, on_complete=close_source, on_error=failed)
            except (RuntimeError, ValueError) as error:
                failed(error)
                self.report({'WARNING'}, rpt_("Return to dock could not start; your pop-out is still open"))
                return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_new_chat(Operator):
    bl_idname = "angelo.new_chat"
    bl_label = "New Chat"

    def execute(self, context):
        try:
            create_session()
        except RuntimeError as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_switch_session(Operator):
    bl_idname = "angelo.switch_session"
    bl_label = "Switch Conversation"
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            show_session(self.session_id)
        except (RuntimeError, ValueError) as error:
            self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_MT_sessions(Menu):
    bl_label = "Conversations"

    def draw(self, context):
        current = get_controller()
        self.layout.operator("angelo.new_chat", text="New Chat", icon='ADD')
        self.layout.operator("angelo.project_brief", text="Project Brief", icon='TEXT')
        self.layout.operator("angelo.performance_summary", text="Performance Summary", icon='TIME')
        self.layout.operator("angelo.checkpoint_settings", text="Snapshot Settings", icon='PREFERENCES')
        self.layout.operator("angelo.checkpoint_retention", text="Review Snapshot Retention", icon='TRASH')
        self.layout.separator()
        for record in _sessions.ordered():
            controller = _controllers.get(record["id"])
            title = chat_titles.session_title(record)
            if controller and controller.pending:
                title = iface_("Approval needed · {title}", title=title)
            elif controller and controller.busy:
                title = iface_("Working · {title}", title=title)
            row = self.layout.row()
            row.operator("angelo.switch_session", text=title, translate=False,
                         icon='CHECKMARK' if record["id"] == current.state["id"] else 'NONE').session_id = record["id"]


class ANGELO_OT_stop(Operator):
    bl_idname = "angelo.stop"
    bl_label = "Stop"
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        controller = _controllers.get(self.session_id) if self.session_id else get_controller()
        if controller is None:
            return {'CANCELLED'}
        from .stop_notice import stop_notice_for
        stopped = stop_notice_for(controller)
        controller.stop()
        controller.emit("message", {"role": "system", **stopped})
        return {'FINISHED'}


class ANGELO_OT_model_picker(Operator):
    bl_idname = "angelo.model_picker"
    bl_label = "Choose Model"
    bl_description = "Models advertised by the selected provider runtime, not a hard-coded OpenAI model list"

    def invoke(self, context, event):
        if not connection_available() and not ready_connections():
            return bpy.ops.angelo.settings()
        if not get_controller().busy and connection_available():
            prepare_controller()
        controller = get_controller()
        refresh_connected_models()
        if not controller.models and not controller.models_loading:
            controller.refresh_models()
        if chat_view.show_popover(context, "models"):
            return {'FINISHED'}
        # Non-chat contexts retain Blender's accessible native menu fallback.
        # The composer never uses this combined stock menu.
        return bpy.ops.wm.call_menu('INVOKE_DEFAULT', name="ANGELO_MT_models")

    def draw(self, context):
        controller = get_controller()
        layout = self.layout
        layout.label(text="Model")
        if controller.busy:
            layout.label(text="Model and effort changes apply to the next turn.")
        current = clean_options(controller.state.get("options"))["model"]
        if controller.models_loading:
            layout.label(text="Loading models from your connection…")
        from .chat_popovers import model_rows
        for row in model_rows(model_choices(controller), current, controller.selection_connection_id):
            if row["kind"] == "provider":  # Provider and model names are data; the empty row is translated.
                layout.separator()
                layout.label(text=row["name"], translate=False)
                continue
            if row["kind"] == "empty":
                layout.label(text=row["name"], translate=False)
                continue
            operator = layout.operator("angelo.select_model", text=row["name"] + (" · " + row["subtitle"] if row["kind"] != "default" and row["subtitle"] else ""), icon='CHECKMARK' if row["selected"] else 'NONE', translate=False)
            if isinstance(row["value"], tuple):
                operator.connection_id, operator.model = row["value"]
            else:
                operator.model = row["value"]
        if controller.models_error:
            for line in chat_view.wrap(controller.models_error[:400], 330, 12, chat_view._font):
                layout.label(text=line[0])
        layout.separator()
        layout.operator("angelo.refresh_models", text="Refresh available models", icon='FILE_REFRESH')
        if controller.selection_provider.startswith("claude_"):
            layout.operator("angelo.custom_model", text="Enter native model ID…")
        layout.separator()
        model = selected_model(controller)
        efforts = model["efforts"] if model and model["efforts"] else ["low", "medium", "high"]
        layout.label(text="Reasoning effort · independent of Fast mode")
        for effort in efforts:
            layout.operator("angelo.set_effort", text=chat_popovers.effort_name(effort), translate=False, icon='CHECKMARK' if effort == clean_options(controller.state.get("options"))["effort"] else 'NONE').effort = effort


class ANGELO_MT_models(Menu):
    bl_label = "Model & Reasoning"

    def draw(self, context):
        ANGELO_OT_model_picker.draw(self, context)


class ANGELO_OT_refresh_models(Operator):
    bl_idname = "angelo.refresh_models"
    bl_label = "Refresh Models"

    def execute(self, context):
        try:
            if not get_controller().busy and connection_available():
                prepare_controller()
            if ready_connections():
                refresh_connected_models(force=True)
            else:
                get_controller().refresh_models()
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_select_model(Operator):
    bl_idname = "angelo.select_model"
    bl_label = "Select Model"
    model: StringProperty(options={'SKIP_SAVE'})
    connection_id: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        return next((m["description"] for m in model_choices(get_controller()) if m["id"] == properties.model and (not properties.connection_id or m.get("connection_id") == properties.connection_id)), "Use the connection's default model")

    def execute(self, context):
        controller = get_controller()
        identity = self.connection_id or controller.selection_connection_id
        if identity:
            try:
                choose_model_connection(controller, identity, self.model)
            except (RuntimeError, ValueError) as error:
                self.report({'ERROR'}, shown(error.args[0]) if error.args and isinstance(error.args[0], Text) else str(error))
                return {'CANCELLED'}
            return {'FINISHED'}
        if self.model and not any(m["id"] == self.model for m in controller.selection_models):
            self.report({'ERROR'}, rpt_("Refresh the models available to this connection"))
            return {'CANCELLED'}
        try:
            options = clean_options(controller.state.get("options"))
            model = next((m for m in controller.selection_models if m["id"] == self.model), next((m for m in controller.selection_models if m["default"]), None))
            effort = options["effort"]
            if model and model["efforts"] and effort not in model["efforts"]:
                effort = model["default_effort"] if model["default_effort"] in model["efforts"] else model["efforts"][0]
            controller.set_options(model=self.model, fast=options["fast"] and bool(model and model["fast_tier"]), effort=effort)
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_custom_model(Operator):
    bl_idname = "angelo.custom_model"
    bl_label = "Native Claude Model ID"
    model: StringProperty(name="Model", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        self.layout.prop(self, "model")
        self.layout.label(text="Claude Code validates access; no automatic fallback.")

    def execute(self, context):
        controller = get_controller()
        if not controller.selection_provider.startswith("claude_") or not self.model.strip():
            return {'CANCELLED'}
        try:
            controller.set_options(model=self.model.strip(), fast=False)
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_set_effort(Operator):
    bl_idname = "angelo.set_effort"
    bl_label = "Set Reasoning Effort"
    effort: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        try:
            get_controller().set_options(effort=self.effort)
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_toggle_fast(Operator):
    bl_idname = "angelo.toggle_fast"
    bl_label = "Fast Mode"
    bl_description = "Use the provider's paid lower-latency tier, when supported. This does not lower reasoning effort"

    def invoke(self, context, event):
        controller = get_controller()
        if clean_options(controller.state.get("options"))["fast"]:
            return self.execute(context)
        model = selected_model(controller)
        if not model or not model["fast_tier"]:
            self.report({'INFO'}, rpt_("Choose a model with an available Fast tier in the model selector first"))
            return bpy.ops.angelo.model_picker('INVOKE_DEFAULT')
        return context.window_manager.invoke_props_dialog(self, width=420, confirm_text=iface_("Enable Fast"))

    def draw(self, context):
        self.layout.label(text="Fast mode uses the provider's lower-latency service tier.")
        self.layout.label(text="It may consume additional quota or incur higher charges.")
        if get_controller().selection_provider.startswith("claude_"):
            self.layout.label(text="Claude subscriptions: paid usage credits, not included usage.")
        self.layout.label(text="The provider controls eligibility and actual latency.")

    def execute(self, context):
        controller = get_controller()
        options = clean_options(controller.state.get("options"))
        model = selected_model(controller)
        if not options["fast"] and (not model or not model["fast_tier"]):
            return {'CANCELLED'}
        try:
            controller.set_options(fast=not options["fast"])
        except RuntimeError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_OT_attach_reference(Operator):
    bl_idname = "angelo.attach_reference"
    bl_label = "Attach Files"
    bl_description = "Attach images, text/code, PDF or 3D assets to the next message. Dropping a file never executes it or imports it into the scene"
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE'})
    filter_glob: StringProperty(default=FILE_FILTER, options={'HIDDEN', 'SKIP_SAVE'})
    session_id: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        controller = get_controller()
        if controller.busy:
            self.report({'INFO'}, rpt_("Wait for this turn to finish before attaching references"))
            return {'CANCELLED'}
        self.session_id = controller.state["id"]
        if self.filepath or self.files:
            return self.execute(context)  # Native FileHandler drop, not a picker.
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        controller = _controllers.get(self.session_id) if self.session_id else get_controller()
        if controller is None or controller.busy:
            self.report({'ERROR'}, rpt_("The target chat is no longer ready for attachments"))
            return {'CANCELLED'}
        paths = [str(Path(self.directory) / item.name) for item in self.files] or [self.filepath]
        try:
            controller.attachments.extend(controller.references.import_images(paths, controller.attachments))
            controller.version += 1
        except (OSError, ValueError) as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class ANGELO_FH_references(bpy.types.FileHandler):
    bl_idname = "ANGELO_FH_references"
    bl_label = "Attach to Angelo Chat"
    bl_import_operator = "angelo.attach_reference"
    bl_file_extensions = ";".join(sorted(FILE_TYPES))

    @classmethod
    def poll_drop(cls, context):
        # Claim drops even while busy, then reject non-destructively in invoke.
        # Otherwise a .blend drop could fall through to Blender's file opener.
        return bool(context.area and console.is_chat(context.area))


class ANGELO_OT_changes(Operator):
    bl_idname = "angelo.changes"
    bl_label = "Scene Changes"
    bl_description = "Actual native read-backs from this chat; not a count of imaginary edited files"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=460)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Native scene changes")
        # Only mutation reports: a scene delta's "changes" is a list of observation rows.
        messages = [m for m in get_controller().state["messages"]
                    if scene_change_report(m.get("result")) or m.get("result", {}).get("file_changes")]
        if not messages:
            layout.label(text="No scene changes recorded in this conversation.")
        for message in messages[-20:]:
            box = layout.box()
            box.label(text=chat_view.tool_title(message))
            if message["result"].get("partial_changes_possible"):
                box.label(text="Operation failed; partial changes may remain.", icon='ERROR')
                box.label(text="Inspect or restore the request snapshot before retrying.")
            if message["result"].get("file_changes"):
                files = message["result"]["file_changes"]
                for path in files["written"]:
                    box.label(text=iface_("Written: {path}", path=path), translate=False)
                for path in files["deleted"]:
                    box.label(text=iface_("Deleted: {path}", path=path), translate=False)
                if message["result"].get("journaled") is True:
                    box.label(text="Restoring a snapshot reverts this file; Blender Undo doesn't.")
                else:
                    box.label(text="Check the workspace files and request snapshot journal.")
                continue
            changes = scene_change_report(message["result"])
            for record in changes["added"][:40]:
                box.label(text="+ " + record["name"] + " · " + record["type"], translate=False)  # Scene data.
            for record in changes["removed"][:40]:
                box.label(text="− " + record["name"], translate=False)
            for record in changes["modified"][:40]:
                box.label(text="~ " + record["name"] + " · " + ", ".join(record["fields"]), translate=False)
            if changes["truncated"]:
                box.label(text="Large scene: details truncated.")
        layout.separator()
        layout.label(text="Operation read-backs, not a live diff after manual edits/Undo.")
        layout.label(text="External effects are not tracked or undoable.")


class ANGELO_OT_restore_checkpoint(Operator):
    bl_idname = "angelo.restore_checkpoint"
    bl_label = "Restore Snapshot"
    bl_description = "Return the scene, and any files the agent changed, to how they were when this snapshot was taken; the chat history is kept"
    checkpoint_id: StringProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        if any(c.busy for c in _controllers.values()):
            self.report({'ERROR'}, rpt_("Stop active requests in all chats before restoring a snapshot"))
            return {'CANCELLED'}
        availability = get_controller().checkpoints.availability(self.checkpoint_id)
        if not availability["available"]:
            self.report({'ERROR'}, rpt_("Request snapshot is unavailable: {reason}", reason=availability["reason"]))
            return {'CANCELLED'}
        from .checkpoint_images import restore_review  # Read once here: the dialog redraws often.
        self._snapshot_review = restore_review(bpy, get_controller(), self.checkpoint_id)
        return context.window_manager.invoke_props_dialog(self, width=500, confirm_text=iface_("Restore snapshot"))

    def draw(self, context):
        from .checkpoint_images import draw_restore  # What is restored, what is not, and the image pixels at stake.
        draw_restore(self.layout, getattr(self, "_snapshot_review", None))

    def execute(self, context):
        from .checkpoint_restore_ui import execute_restore
        return execute_restore(sys.modules[__name__], self)


class ANGELO_OT_remove_reference(Operator):
    bl_idname = "angelo.remove_reference"
    bl_label = "Remove Reference"
    index: IntProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        controller = get_controller()
        if not controller.busy and 0 <= self.index < len(controller.attachments):
            reference = controller.attachments.pop(self.index)
            try:
                controller.references.remove(reference)
            except (OSError, ValueError):
                pass
            controller.version += 1
        return {'FINISHED'}


class ANGELO_OT_approve(Operator):
    bl_idname = "angelo.approve"
    bl_label = "Approve Blender Change"
    call_id: StringProperty(options={'SKIP_SAVE'})
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        controller = _controllers.get(self.session_id) if self.session_id else get_controller()
        if controller is None:
            return {'CANCELLED'}
        call = controller.pending.get(self.call_id)
        if call is None or not call.approval:
            return {'CANCELLED'}
        if controller.expire_tool(call):
            self.report({'INFO'}, rpt_("This operation/approval expired; no change was applied"))
            return {'CANCELLED'}
        if call.name == "angelo_execute_python" and not controller.script_authorized(call):
            self.report({'INFO'}, rpt_("Read the script shown in the chat, then click Approve to run it"))
            bpy.ops.angelo.review_tool(call_id=call.id)
            return {'CANCELLED'}
        if call.name == "angelo_accept_preview":
            from .agent_workspace_ui import approval_preview_reviewed
            if not approval_preview_reviewed(controller, call):
                return bpy.ops.angelo.accept_native_preview('INVOKE_DEFAULT',
                    preview_id=call.arguments["preview_id"], call_id=call.id, session_id=controller.state["id"])
        if call.name in {"angelo_memory_update", "angelo_discard_preview"}:
            # Saving public notes or deleting an isolated disk stage cannot
            # become an Undo entry or require authority to edit the document.
            controller.execute_tool(call, BlenderExecutor(main_window()), document_key())
            return {'FINISHED'}
        stage_only = call.name in {"angelo_scene_program", "angelo_apply_recipe"} and call.arguments.get("mode") == "PREVIEW"
        if not stage_only and call.name not in ((STUDIO_NAMES - {"angelo_studio_import_3d"}) | {"angelo_delegate", "angelo_set_workspace_mode"}) and not context.preferences.edit.use_global_undo:
            controller.finish_tool(call, {"error": "Enable Blender's Global Undo before applying an agent scene change"})  # i18n: tool result returned to the model
            return {'CANCELLED'}
        # Preparation and cooperative staging are not scene mutations. Only
        # the final commit/legacy execution operator owns the one Undo step.
        if controller.approve_tool(call, BlenderExecutor(main_window()), document_key()):
            return {'FINISHED'}
        return getattr(bpy.ops.angelo, script_errors.apply_operator(bpy, main_window(), call.name))('EXEC_DEFAULT', True,
            call_id=call.id, session_id=controller.state["id"])


class ANGELO_OT_apply_approved_tool(Operator):
    bl_idname = "angelo.apply_approved_tool"
    bl_label = "Apply Approved Blender Change"
    bl_options = {'UNDO'}
    call_id: StringProperty(options={'SKIP_SAVE'})
    session_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        controller = _controllers.get(self.session_id) if self.session_id else get_controller()
        call = controller.pending.get(self.call_id) if controller else None
        if call is None or not call.approval or controller.expire_tool(call):
            return {'CANCELLED'}
        if call.name == "angelo_execute_python" and not controller.script_authorized(call):
            self.report({'ERROR'}, rpt_("Review the exact privileged script before applying it"))
            return {'CANCELLED'}
        if call.name not in ((STUDIO_NAMES - {"angelo_studio_import_3d"}) | {"angelo_delegate", "angelo_set_workspace_mode"}) and not context.preferences.edit.use_global_undo:
            self.report({'ERROR'}, rpt_("Enable Blender's Global Undo before applying an agent scene change"))
            return {'CANCELLED'}
        # This internal operator is never a second route around the approval
        # gate. execute_tool repeats document/precondition checks immediately.
        result = controller.execute_tool(call, BlenderExecutor(main_window()), document_key())
        return {'CANCELLED'} if isinstance(result, dict) and result.get("main_data_restored") else {'FINISHED'}  # Main was reloaded; pixels/external effects may remain.


class ANGELO_OT_review_tool(Operator):
    bl_idname = "angelo.review_tool"
    bl_label = "Review Change Details"
    call_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        controller = get_controller()
        if self.call_id not in controller.pending:
            return {'CANCELLED'}
        controller.review_script(controller.pending[self.call_id])
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if console.is_chat(area):
                    state = chat_view._views.setdefault(area.as_pointer(), {})
                    state.setdefault("expanded", set()).add(("approval", self.call_id))
                    area.tag_redraw()
        return {'FINISHED'}


class ANGELO_OT_decline(Operator):
    bl_idname = "angelo.decline"
    bl_label = "Decline Blender Change"
    call_id: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        get_controller().decline_tool(self.call_id)
        return {'FINISHED'}


class ANGELO_OT_multiline(Operator):
    bl_idname = "angelo.multiline"
    bl_label = "Insert Chat Line Break"

    def execute(self, context):
        if console.is_chat(context.area):
            bpy.ops.console.insert(text="\n")
            return {'FINISHED'}
        return {'PASS_THROUGH'}


class ANGELO_MT_chat(Menu):
    bl_label = "Chat"

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.enabled = not get_controller().busy
        row.operator("angelo.new_chat", icon="FILE_NEW")
        layout.separator()
        row = layout.row()
        row.enabled = not get_controller().busy
        row.operator("angelo.settings", text="Connection Settings", icon="PREFERENCES")


class ANGELO_MT_view(Menu):
    bl_label = "View"

    def draw(self, context):
        layout = self.layout
        from . import director
        if director.is_director(context.window):
            layout.operator("angelo.workspace_mode", text="Show Controls").mode = "MANUAL"
            layout.prop(context.space_data, "font_size", text="Text Size")
            return
        layout.operator("angelo.workspace_mode", text="Director Mode").mode = "DIRECTOR"
        layout.separator()
        layout.operator("angelo.dock_side", text="Dock Left").side = "LEFT"
        layout.operator("angelo.dock_side", text="Dock Right").side = "RIGHT"
        layout.operator("angelo.pop_out")
        layout.operator("angelo.return_dock")
        layout.separator()
        layout.prop(context.space_data, "font_size", text="Text Size")
        layout.menu("INFO_MT_area")


def draw_header(self, context):
    from . import studio
    if studio.is_studio(context.area):
        self.layout.template_header()
        self.layout.label(text="Generative AI")
        self.layout.separator_spacer()
        self.layout.menu("INFO_MT_area", text="View")
        return
    if not console.is_chat(context.area):
        _header_draw(self, context)
        return
    layout = self.layout
    from . import director
    if not director.is_director(context.window):
        layout.template_header()
    controller = get_controller()
    title = chat_titles.session_title(_sessions.records[controller.state["id"]])
    layout.menu("ANGELO_MT_sessions", text=title[:36] + ("…" if len(title) > 36 else ""), translate=False)
    layout.operator("angelo.new_chat", text="", icon='ADD')
    if not director.is_director(context.window):
        layout.menu("ANGELO_MT_view")
    pending = next((call for call in controller.pending.values() if call.approval), None)  # Approved in the chat card only.
    if controller.busy:
        layout.operator("angelo.stop", text="", icon="CANCEL")
    layout.separator_spacer()
    layout.operator("angelo.settings", text="", icon='PREFERENCES')
    _header_states[context.area.as_pointer()] = (controller.status, controller.busy, pending.id if pending else None)


@keep_alive(0.1)  # Blender drops a timer that raises; the chat must keep following async changes.
def pump():
    global _tool_pump_cursor
    from .memory_project_ui import pump as prepare_project_memory
    prepare_project_memory(sys.modules[__name__])
    from . import always_allow, external_host, prewarm_ui, snapshot_service
    always_allow.sync()  # Before any dispatch: a policy that arrives or lifts reaches the next script.
    external_host.pump(sys.modules[__name__])
    snapshot_service.pump(sys.modules[__name__])
    prewarm_ui.pump(sys.modules[__name__])  # The next message's runtime starts while it is written.
    store = connections()
    previous = dict(store.records)
    if store.reload_if_changed():
        for identity in previous.keys() - store.records.keys():
            _memory_keys.pop(identity, None)
            saved_keys_ui.discard(identity)  # The removing instance deletes the saved key.
            _api_connections.invalidate(identity)
            catalogs().invalidate(identity)
            for controller in _controllers.values():
                controller.invalidate_runtime(identity)
                if controller.state.get("connection_id") == identity:
                    controller.api_key = ""
                    controller.connection_error = True
        for identity, record in store.records.items():
            if record != previous.get(identity) and record["mode"] in {"codex_subscription", "codex_api", "claude_subscription"}:
                scope = record["mode"] if record["reuse_native"] else identity
                if scope not in _authentication.running:
                    try:
                        _authentication.start(record["mode"], connection_settings(record["mode"], record=record), state_directory(), identity=scope)
                    except Exception:
                        _authentication.status[scope] = Text(n_("Unable to check native sign-in"))
        console.redraw_chats(_controllers.values())
        preferences_ui.redraw()
    if saved_keys_ui.pump(sys.modules[__name__]):
        preferences_ui.redraw()
    team_account_ui.pump(sys.modules[__name__])
    api_changes = _api_connections.pump()
    if api_changes:
        for identity in api_changes:
            record = connections().records.get(identity)
            result = _api_connections.results.get(identity)
            if record and is_coding(record) and result and result.state == "verified":
                catalogs().entries[identity] = list(result.models)
                catalogs().errors.pop(identity, None)
                for controller in _controllers.values():
                    if controller.state.get("connection_id") == identity:
                        controller.models = list(result.models)
                        controller.models_error = ""
                        controller.connection_error = False
                    if controller._next_binding and controller.selection_connection_id == identity:
                        controller._next_binding["models"] = list(result.models)
            console.redraw_chats(_controllers.values())
        preferences_ui.redraw()
    changes = _authentication.pump()
    if changes:
        for identity, logged_in in changes:
            mode = _authentication.modes.get(identity, identity)
            record = connections().records.get(identity)
            status = _authentication.status.get(identity, "")
            if identity == mode and native_suggestions.usable(status):
                # Discovery only suggests (draw_native_suggestions): no connection
                # is created and no chat is bound. Chats already bound to a
                # connection that reuses this sign-in load its models.
                record, waiting = native_suggestions.chats_to_refresh(connections(), _controllers.values(), mode, status)
                for controller in waiting:
                    controller.settings = connection_settings(mode, record=record)
                    controller.refresh_models()
            from . import codex_runtime
            if not logged_in and mode.startswith("codex_") and (status == "Not connected"
                                                                or status.startswith("Different native billing mode")):
                # A status check found no ChatGPT account: no shared app-server keeps serving the old login.
                codex_runtime.retire_connection(record["id"]) if record else codex_runtime.retire_login(
                    codex_runtime.login_file({}))
            if logged_in:
                if record:
                    catalogs().invalidate(record["id"])
                else:  # The default (reused native) login: no app-server of the mode keeps the old account.
                    codex_runtime.retire_mode(mode)
                # A sign-in may change the account. Preserve local histories,
                # but never resume an old native thread under a new account.
                for controller in _controllers.values():
                    if record:
                        controller.invalidate_runtime(record["id"])
                    if controller.state["provider"] == mode and (not record or controller.state.get("connection_id") == record["id"]):
                        if controller.provider:
                            import threading
                            threading.Thread(target=controller.provider.close, daemon=True).start()
                        controller.provider = None
                        controller.state["native_id"] = None
                        controller.connection_error = False
                        controller.save()
                _authentication.start(mode, connection_settings(mode, record=record), state_directory(), identity=identity)
        console.redraw_chats(_controllers.values())  # A CLI found, signed in or out: the connect card changes.
        preferences_ui.redraw()
    if _controller:
        catalog = catalogs()
        catalog_changed = catalog.pump()
        window = main_window()
        controllers = list(_controllers.values())
        for controller in controllers:
            controller.pump_events()
            if controller.unsent_draft:
                from .drafts import restore_unsent
                from .editor_types import read_chat_draft, write_chat_draft
                restore_unsent(controller, active=controller is _controller, spaces=[space for _, space in console_spaces()],
                               saved=_drafts, read=read_chat_draft, write=write_chat_draft)
            controller.apply_next_connection()
            identity = controller.state.get("connection_id")
            if catalog_changed and identity in catalog.entries:
                controller.models = list(catalog.entries[identity])
                controller.models_error = catalog.errors.get(identity, "")
                controller.version += 1
            if catalog_changed and controller._next_binding and controller.selection_connection_id in catalog.entries:
                controller._next_binding["models"] = list(catalog.entries[controller.selection_connection_id])
                controller.version += 1
            catalog.seed(identity, controller.models)
            _sessions.note(controller.state)  # The switcher index, written at most once a second below.
        if window and controllers:
            from . import agent_target_labels
            for target in controllers:
                agent_target_labels.prepare(target, window, document_key())
            # One shared native-operation budget across every chat. Advance
            # from the last chat that admitted work, rather than from empty
            # chats, so a busy background session cannot starve another one.
            def apply_mutation(target, call):
                with bpy.context.temp_override(window=window):
                    bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
            def commit_mutation(target, call):
                with bpy.context.temp_override(window=window):
                    return bpy.ops.angelo.commit_native_job('EXEC_DEFAULT', True,
                        call_id=call.id, session_id=target.state["id"])
            _tool_pump_cursor = pump_controller_tools(
                controllers, _tool_pump_cursor, BlenderExecutor(window), document_key(), apply_mutation,
                commit_mutation=commit_mutation)
        from .native_preview_images import prepare
        prepare([_controller, *(target for target in controllers if target is not _controller)])
        refresh_connected_models()
        _sessions.save_if_due()
        # A sign-in failure shows Connect in the chat (chat_welcome); Preferences never opens by itself.
        _controller.setup_required = False
        from . import quick_connect
        if quick_connect.pump(sys.modules[__name__]):
            console.redraw_chats(controllers)  # The chat's connect card.
        for window in list(bpy.context.window_manager.windows):
            for area in window.screen.areas:
                if console.is_chat(area):
                    pending = next((call for call in _controller.pending.values() if call.approval), None)
                    header_state = (_controller.status, _controller.busy, pending.id if pending else None)
                    if _header_states.get(area.as_pointer()) != header_state:
                        # Header layout is separate from Console textview redraws.
                        for header in area.regions:
                            if header.type == "HEADER":
                                header.tag_redraw()
                    region = next((r for r in area.regions if r.type == "WINDOW"), None)
                    if region:
                        with bpy.context.temp_override(window=window, area=area, region=region):
                            console.render(bpy.context)
                        # Native editor selection can open chat in another
                        # window without calling the compatibility operator.
                        # Start its input handler here, never inside drawing.
                        chat_view.ensure_input(window, area)
    # A short timer interval lets Blender process input/redraws between native
    # operations without adding a full idle interval to every queued program.
    if any(c.has_dispatchable_tools for c in _controllers.values()):
        return 0.001
    return 0.02 if any(c.busy for c in _controllers.values()) else 0.1


@persistent
def document_load_pre(_):
    global _document_serial, _recovery_original_path
    _document_serial = str(uuid.uuid4())
    from . import scene_queries, agent_workspace_ui, change_feed, view_cache
    scene_queries.invalidate()
    view_cache.clear()
    change_feed.document_reset("load")
    agent_workspace_ui._approval_reviews.clear()
    from .native_preview_images import clear
    clear()
    from . import agent_target_labels
    agent_target_labels.clear()
    from .memory_project_ui import clear
    clear()
    if _restoring_snapshot:
        return
    _recovery_original_path = ""
    if _controller:
        for controller in _controllers.values():
            controller.set_document(document_key())


@persistent
def document_load_post(_):
    from .program_storage import retire_native_residue_after_load
    retire_native_residue_after_load(document_key())
    # Blender drops custom font IDs on .blend load even with load_ui=False.
    # Reload before pump/redraw; invalid metrics otherwise hide chat text and
    # incorrectly suppress wrapping in subsequent native review dialogs.
    chat_view.reload_fonts()
    if not _restoring_snapshot:
        from .memory_project_ui import schedule
        for controller in _controllers.values():
            schedule(sys.modules[__name__], controller)


@persistent
def document_save_post(_):
    if _capturing_checkpoint or _restoring_snapshot:
        return
    from .memory_project_ui import schedule
    for controller in _controllers.values():
        schedule(sys.modules[__name__], controller)


def invalidate_workspace_state(reason):
    """Undo replaces live IDs; invalidate observations and historical handles."""
    from . import scene_queries, agent_workspace_ui, change_feed, view_cache
    scene_queries.invalidate()
    view_cache.clear()
    change_feed.document_reset(reason)
    agent_workspace_ui._approval_reviews.clear()
    from .native_preview_images import clear
    clear()
    from . import agent_target_labels
    agent_target_labels.clear()
    for controller in tuple(_controllers.values()):
        controller.cancel_tools("Native state changed by " + reason + "; uncommitted work was cancelled")  # i18n: tool-result error returned to the model
        controller.invalidate_native_stages("Native state changed by " + reason)  # i18n: tool-result error returned to the model
        controller.mark_memory_stale(reason)


@persistent
def workspace_undo_post(_):
    invalidate_workspace_state("undo")


@persistent
def workspace_redo_post(_):
    invalidate_workspace_state("redo")


def window_menu(self, context):
    self.layout.separator()
    self.layout.operator("angelo.open_chat", text="Angelo Chat")
    self.layout.operator("angelo.workspace_mode", text="Director Mode").mode = "DIRECTOR"


CLASSES = [ANGELO_Preferences, ANGELO_OT_settings, ANGELO_OT_api_key, ANGELO_OT_copy_login,
           ANGELO_OT_new_connection, ANGELO_OT_connection_use, ANGELO_OT_connection_menu,
           ANGELO_OT_connection_check, ANGELO_OT_connection_load_key, ANGELO_OT_connection_signin, ANGELO_OT_connection_remove,
           ANGELO_OT_dismiss_native_suggestion,
           ANGELO_OT_use_connection, ANGELO_OT_native_signin, ANGELO_OT_check_signin, ANGELO_OT_cancel_signin, ANGELO_OT_open_chat,
           ANGELO_OT_dock_side, ANGELO_OT_pop_out, ANGELO_OT_return_dock, ANGELO_OT_new_chat,
           ANGELO_OT_switch_session, ANGELO_MT_sessions,
           ANGELO_OT_model_picker, ANGELO_MT_models, ANGELO_OT_refresh_models, ANGELO_OT_select_model, ANGELO_OT_custom_model,
           ANGELO_OT_set_effort, ANGELO_OT_toggle_fast, ANGELO_MT_approval, ANGELO_OT_set_approval, ANGELO_OT_trusted_scripts,
           ANGELO_OT_attach_reference, ANGELO_FH_references, ANGELO_OT_remove_reference, ANGELO_OT_changes, ANGELO_OT_restore_checkpoint,
           ANGELO_OT_stop, ANGELO_OT_approve, ANGELO_OT_apply_approved_tool, script_errors.grouped(ANGELO_OT_apply_approved_tool), ANGELO_OT_review_tool, ANGELO_OT_decline, ANGELO_OT_multiline,
           ANGELO_MT_chat, ANGELO_MT_view]


def register():
    global _header_draw, _authentication, _api_connections, _transient_preferences
    if _authentication.closed:
        _authentication = NativeAuthentication()
    if _api_connections.closed:
        _api_connections = ApiConnections()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # Temporary addon enablement does not create its AddonPreferences record.
    # Give the prototype actual settings without saving/installing preferences.
    if __package__ not in bpy.context.preferences.addons:
        entry = bpy.context.preferences.addons.new()
        entry.module = __package__
        _transient_preferences = True
    from . import always_allow
    always_allow.attach(state_directory())   # The person's saved choice (off when never set).
    preferences_ui.register()
    from . import agent_workspace_ui
    agent_workspace_ui.register()
    from . import native_programs
    native_programs.register()
    chat_view.register()
    from . import quick_connect, voice_ui, intro
    quick_connect.register()
    voice_ui.register()
    intro.register()
    from . import studio
    studio.register()
    from . import flow_ui
    flow_ui.register()
    from . import director
    director.register()
    connections()
    for mode in ("codex_subscription", "claude_subscription", "codex_api"):
        try:
            _authentication.start(mode, connection_settings(mode), state_directory())
        except Exception:
            _authentication.status[mode] = Text(n_("Native runtime unavailable"))
    for record in connections().records.values():
        if not record["reuse_native"] and record["mode"] in {"codex_subscription", "codex_api", "claude_subscription"}:
            try:
                _authentication.start(record["mode"], connection_settings(record["mode"], record=record), state_directory(), identity=record["id"])
            except Exception:
                _authentication.status[record["id"]] = Text(n_("Not connected"))
    # Prompt-free loads on the saved-key worker (Keychain, Credential Manager or keyring); results apply in pump().
    saved_keys_ui.start(sys.modules[__name__], background=bpy.app.background)
    team_account_ui.register()  # A stored team sign-in resumes on its worker; team connections appear in pump().
    team_account_ui.start(sys.modules[__name__], background=bpy.app.background)
    _header_draw = bpy.types.CONSOLE_HT_header.draw
    header_type = bpy.types.CONSOLE_HT_header
    bpy.utils.unregister_class(header_type)
    header_type.draw = draw_header
    bpy.utils.register_class(header_type)
    from . import editor_types
    editor_types.register()
    bpy.types.TOPBAR_MT_window.append(window_menu)
    bpy.app.handlers.load_pre.append(document_load_pre)
    bpy.app.handlers.load_post.append(document_load_post)
    bpy.app.handlers.save_post.append(document_save_post)
    bpy.app.handlers.undo_post.append(workspace_undo_post)
    bpy.app.handlers.redo_post.append(workspace_redo_post)
    bpy.app.handlers.depsgraph_update_post.append(track_scene_changes)
    bpy.app.timers.register(pump, first_interval=0.1, persistent=True)
    from . import snapshot_service
    snapshot_service.start(state_directory())
    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        keymap = keyconfig.keymaps.new(name="Console", space_type="CONSOLE")
        item = keymap.keymap_items.new("angelo.multiline", 'RET', 'PRESS', shift=True)
        _keymaps.append((keymap, item))
        # Shift+F4 must always mean Python Console, not the cached chat surface.
        if not editor_types.native_picker():
            keymap = keyconfig.keymaps.new(name="Window", space_type="EMPTY")
            item = keymap.keymap_items.new("angelo.editor_type", 'F4', 'PRESS', shift=True)
            item.properties.ui_type = "CONSOLE"
            _keymaps.append((keymap, item))


def unregister():
    global _controller, _layout_timer, _sessions, _transient_preferences, _connections, _catalogs
    from . import always_allow, external_host
    always_allow.detach()  # A disabled add-on keeps no consent; registering again reads the saved choice.
    external_host.shutdown()
    if _layout_timer is not None and bpy.app.timers.is_registered(_layout_timer):
        bpy.app.timers.unregister(_layout_timer)
    _layout_timer = None
    from . import director
    director.unregister()
    _dock_metadata.clear()
    # Disabling the host leaves real Python consoles, not orphaned agent
    # languages that could bootstrap the host again from their next banner.
    from . import editor_types
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            from . import studio
            if console.is_chat(area) or studio.is_studio(area):
                editor_types.select_console(window, area, coding=False)
    from . import preview_branding
    preview_branding.uninstall()
    editor_types.unregister()
    from . import flow_ui
    flow_ui.unregister()
    studio.unregister()
    chat_view.unregister()
    from . import quick_connect, voice_ui, intro
    intro.unregister()
    quick_connect.unregister()
    voice_ui.unregister()
    from .memory_project_ui import clear
    clear()
    from . import view_cache
    view_cache.clear()
    from . import agent_workspace_ui
    agent_workspace_ui.unregister()
    from . import native_programs
    native_programs.unregister()
    preferences_ui.unregister()
    _authentication.close()
    _api_connections.close()
    saved_keys_ui.close()
    team_account_ui.unregister()
    if _catalogs:
        _catalogs.close()
        _catalogs = None
    if bpy.app.timers.is_registered(pump):
        bpy.app.timers.unregister(pump)
    if document_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(document_load_pre)
    if document_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(document_load_post)
    if document_save_post in bpy.app.handlers.save_post:
        bpy.app.handlers.save_post.remove(document_save_post)
    if workspace_undo_post in bpy.app.handlers.undo_post:
        bpy.app.handlers.undo_post.remove(workspace_undo_post)
    if workspace_redo_post in bpy.app.handlers.redo_post:
        bpy.app.handlers.redo_post.remove(workspace_redo_post)
    if track_scene_changes in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(track_scene_changes)
    bpy.types.TOPBAR_MT_window.remove(window_menu)
    if _header_draw:
        header_type = bpy.types.CONSOLE_HT_header
        bpy.utils.unregister_class(header_type)
        header_type.draw = _header_draw
        bpy.utils.register_class(header_type)
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()
    from . import snapshot_service
    snapshot_service.stop()  # Uncommitted ingestion is discarded; full copies stay authoritative.
    for controller in _controllers.values():
        controller.close()
        _sessions.update(controller.state)
    if _sessions:
        _sessions.save()
    _controllers.clear()
    _controller = _sessions = None
    _connections = None
    _drafts.clear()
    _memory_keys.clear()
    console._versions.clear()
    console._states.clear()
    from . import change_feed
    change_feed.clear()
    _header_states.clear()
    if _transient_preferences:
        entry = bpy.context.preferences.addons.get(__package__)
        if entry:
            bpy.context.preferences.addons.remove(entry)
        _transient_preferences = False
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
