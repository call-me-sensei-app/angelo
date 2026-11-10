# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer's + menu as one registry: every place Angelo's chat bar leads to (1.0.5). No Blender here.

The chat input bar is Angelo's main navigation, in Director and in Manual (maintainer, 1.0.5): from its + button a
person reaches attachments, Media Studio, Generation Flows, the base meshes and every later feature. A feature adds a
``Feature`` with ``register`` instead of a button somewhere else; ``chat_add_menu`` draws whatever is registered.

A feature is either one row that runs an operator (Media Studio), or a **group** whose rows (``children``) the menu
shows as a page of their own (Generation Flows, the base meshes): a group never opens inside the list, so no group, open
or long (21 base meshes, every saved flow), can push another feature out of view (1.0.8; inline groups remembered
open across launches left 6 of 19 features in view). ``placement`` says where the launcher draws a feature: a tile in
the grid, beside the search field (``bar``: Attach) or as a text button in the footer (Report a bug).
``TOP_LEVEL`` names the features Angelo ships; host tests check each is registered by its module and is in view, with
no scrolling, at the common laptop sizes in every language (``chat_add_layout``).

Row dicts (drawn by ``chat_add_menu``): ``kind`` ``section`` / ``note`` / ``toggle`` / ``row`` / ``group``;
``action`` ``popup_add_open`` (run ``operator`` with ``properties``), ``popup_add_group`` (open or close ``value``),
or a feature's own (``popup_add_attach``, ``popup_add_pick``, ``popup_add_fit``, ``popup_add_choose``: a tool
with one option, chosen in one click); ``label`` / ``subtitle`` are ``n_``
constants or (template, fields) pairs, translated where drawn, except a label whose ``translate`` is False (a
flow's or an asset's name); ``glyph`` names the tile; ``depth`` 1 for a group's rows.
"""

from dataclasses import dataclass, field

from .ui_text import n_

# The features Angelo ships, in menu order. A module that adds one registers it at its own register(); a feature
# from another branch adds its id here with its registration (test_native_chat_destinations checks both).
TOP_LEVEL = ("attach", "goal", "media_studio", "photo_to_3d", "library", "video_tools", "flows", "render_engine", "render_tools",
             "workflows", "base_mesh", "hands", "select_through", "tension_map", "uv_checker", "engine_skeleton", "web_export",
             "web_preview", "godot_link", "apps_settings", "report_bug", "request_feature")
# Features drawn inside a built-in group rather than as rows of their own (1.0.8: six Create rows for video and
# rendering pushed the base meshes and Workflows below the fold). A member whose group is not registered is drawn at
# the top level, so a feature is never hidden.
VIDEO_GROUP, RENDER_GROUP = "video_tools", "render_tools"
PARENTS = {"video": VIDEO_GROUP, "shot_to_video": VIDEO_GROUP, "shot_motion": VIDEO_GROUP,
           "ai_render": RENDER_GROUP, "render_region": RENDER_GROUP, "bake_maps": RENDER_GROUP}
# Every feature Angelo ships, whether it is a row of its own or a group's member.
SHIPPED = TOP_LEVEL + tuple(PARENTS)

ATTACH = n_("Attach")
CREATE = n_("Create")
START_FROM = n_("Start from")
PLACEMENTS = ("grid", "bar", "footer")


@dataclass(frozen=True)
class Feature:
    id: str
    title: str                 # n_: what the row says.
    why: str = ""              # n_: what the person gets, one line.
    glyph: str = "dot"         # The tile drawn beside it (chat_add_menu.draw_glyph).
    order: int = 100           # Menu position; built-ins use 0-40.
    section: str = ""          # n_: a heading drawn above it when it differs from the previous feature's.
    operator: str = ""         # A single row: the operator its click runs (INVOKE_DEFAULT) ...
    properties: tuple = ()     # ... with these ((name, value), ...).
    action: str = ""           # Or a feature-owned action instead of an operator (attach).
    children: object = None    # A group: callable(chosen, fit_target) -> its rows while open.
    lead_only: bool = False    # A tool performed at Send on Angelo's own scene: never offered in a worker's chat.
    placement: str = "grid"    # Where the launcher draws it: a grid tile, beside search ("bar") or in the footer.
    owner: str = field(default="", compare=False)   # The registering module, for the coverage test.


_features = {}


def register(feature):
    """Add or replace ``feature`` (by id)."""
    if not feature.id or (feature.children is None) == (not (feature.operator or feature.action)):
        raise ValueError("A feature is one row with an operator or action, or a group with children")
    if feature.placement not in PLACEMENTS or (feature.placement != "grid" and feature.children is not None):
        raise ValueError("A group is a grid tile; a feature is placed in the grid, the bar or the footer")
    _features[feature.id] = feature


def unregister(feature_id):
    _features.pop(feature_id, None)


def features():
    return sorted(_features.values(), key=lambda item: (item.order, item.id))


def feature(feature_id):
    return _features.get(feature_id)


def _row(item):
    return {"kind": "row", "action": item.action or "popup_add_open", "value": item.id, "label": item.title,
            "subtitle": item.why, "glyph": item.glyph, "feature": item.id, "operator": item.operator,
            "properties": item.properties, "placement": item.placement}


def _offered(item, worker):
    return not (worker and item.lead_only)


def members(group_id, worker=False):
    """The registered features drawn inside ``group_id`` (PARENTS), in menu order."""
    return [item for item in features() if PARENTS.get(item.id) == group_id and _offered(item, worker)]


def _members_of(group_id):
    return lambda chosen, fit_target, worker=False: [_row(item) for item in members(group_id, worker)]


def entries(worker=False):
    """The menu's top level, in order: one dict per feature (a row, or a ``group`` whose rows ``group_rows`` gives),
    each with the ``section`` heading it is drawn under (a feature with none stays under the one before it). A
    worker's chat (``worker``) works on its worker's document, so it is offered no ``lead_only`` tool: Send would not
    run it. A built-in group with no member to offer is left out; a member whose group is not registered is a row of
    its own, so no feature is ever hidden."""
    found, section = [], ""
    for item in features():
        if not _offered(item, worker) or PARENTS.get(item.id) in _features:
            continue
        if item.id in (VIDEO_GROUP, RENDER_GROUP) and not members(item.id, worker):
            continue
        if item.placement == "grid":   # Only the grid's headings carry over: the bar and footer have none.
            section = item.section or section
        if item.children is None:
            found.append(dict(_row(item), section=section if item.placement == "grid" else item.section))
        else:
            found.append({"kind": "group", "action": "popup_add_group", "value": item.id, "label": item.title,
                          "subtitle": item.why, "glyph": item.glyph, "feature": item.id, "section": section,
                          "placement": "grid"})
    return found


def group_rows(group_id, chosen=(), fit_target="", worker=False):
    """The rows of one group's page (``children``), each knowing its feature: a built-in group's members are features
    of their own, any other group's rows belong to the group."""
    item = _features.get(group_id)
    if item is None or item.children is None or not _offered(item, worker):
        return []
    if group_id in (VIDEO_GROUP, RENDER_GROUP):
        return [dict(row, feature=row.get("feature", group_id)) for row in item.children(chosen, fit_target, worker)]
    return [dict(row, feature=group_id) for row in item.children(chosen, fit_target)]


def menu_rows(chosen=(), fit_target="", open_groups=(), worker=False):
    """Every registered feature's rows top to bottom, a section heading where it changes, and a group's rows after it
    while ``open_groups`` has it: the whole menu as one list, which search reads (every group open). The launcher
    draws the top level (``entries``) and each group as a page of its own (``group_rows``)."""
    rows, section = [], None
    for entry in entries(worker):
        if entry["section"] and entry["section"] != section:
            rows.append({"kind": "section", "label": entry["section"]})
            section = entry["section"]
        if entry["kind"] != "group":
            rows.append(entry)
            continue
        rows.append(dict(entry, open=entry["value"] in open_groups))
        if entry["value"] in open_groups:
            rows += [dict(row, depth=1) for row in group_rows(entry["value"], chosen, fit_target, worker)]
    return rows


# The built-in groups (PARENTS), registered with the registry itself.
register(Feature("video_tools", n_("Video"), n_("Generate a clip, or make one from a camera's shot or move"),
                 glyph="video", order=15, section=CREATE, children=_members_of(VIDEO_GROUP), owner="chat_destinations"))
register(Feature("render_tools", n_("Render"), n_("AI render, a region over the last render, or every map at once"),
                 glyph="region", order=22, section=CREATE, children=_members_of(RENDER_GROUP),
                 owner="chat_destinations"))


def leaf(feature_id, row_id, title, subtitle="", glyph="dot", operator="", properties=(), translate=True):
    """One row inside a group that runs an operator (a flow, a template); ``row_id`` is unique in the menu."""
    return {"kind": "row", "action": "popup_add_open", "value": row_id, "label": title, "subtitle": subtitle,
            "glyph": glyph, "operator": operator, "properties": tuple(properties), "translate": translate,
            "feature": feature_id}


def _attach_feature():
    return Feature("attach", n_("Image or file…"), n_("Show the agent a reference picture or model"), glyph="attach",
                   order=0, section=ATTACH, action="popup_add_attach", placement="bar", owner="chat_destinations")


def _base_mesh_feature():
    from . import chosen_tools
    item = chosen_tools.tool("base_mesh")
    return Feature("base_mesh", n_("Base meshes"), item.why, glyph="base_mesh", order=40, section=START_FROM,
                   children=chosen_tools.base_mesh_rows, lead_only=True, owner="chosen_tools")


def _hands_feature():
    from . import chosen_tools
    item = chosen_tools.tool("hands")
    return Feature("hands", item.title, item.why, glyph="base_mesh", order=45, action="popup_add_choose",
                   lead_only=True, owner="chosen_tools")


# Apps and settings (1.0.10): connecting an AI app to Angelo, team sign-in and the snapshot settings were reachable
# only from Preferences, the status bar and the chat header's settings menu. One Share tile (it fills Share's empty
# half-row, so the menu grows by nothing) opens their rows; each row shows the state its click changes.
APPS_SETTINGS = n_("Apps and settings")
APPS_SETTINGS_WHY = n_("Connect Claude Code or Codex, sign in to your Angelo team, or change snapshot settings")
CONNECT_APP = n_("Connect {app}")   # The AI Providers card's button.
CONNECT_APP_WHY = n_("{app} can then read and edit your scene; you approve its changes")
APP_CONNECTED = n_("{app} is connected")
APP_CONNECTED_WHY = n_("Disconnect it, or see the script files it may run, in AI Providers")
APP_MISSING_WHY = n_("Not found on this computer; AI Providers has its setup to copy")
NOT_ON_WINDOWS = n_("Coming to Windows in a later update.")   # The AI Providers card's words (external_ui gate).
TEAM_SIGN_IN = n_("Sign in to Angelo Team")   # The sign-in operator's own label (team_account_ui).
TEAM_SIGN_IN_WHY = n_("Use the AI providers and library your organization shares")
PROVIDERS = n_("AI Providers…")
PROVIDERS_WHY = n_("See what is connected, and change it")
SNAPSHOT_SETTINGS = n_("Snapshot settings…")
SNAPSHOT_SETTINGS_WHY = n_("Choose when snapshots are taken and how much disk space they may use")
SNAPSHOT_REVIEW = n_("Remove old snapshots…")
SNAPSHOT_REVIEW_WHY = n_("See which snapshots your limits would remove; nothing goes until you confirm")
CONNECTED_APPS = ("claude_code", "codex")   # The apps Angelo sets up itself (external_ui.OFFERED).
_registry_seen = {}   # The apps' states (``_app_states``), kept between draws.
_INSTALL_CHECK = 2.0


def _team_sign_in_offered():
    """Whether the person is signed out of their Angelo team, the one state its sign-in button is offered in (the
    status bar's rule: not while signed in, waiting for the browser, restoring or offline)."""
    try:
        from . import team_account_ui
        from .team_view import SIGNED_OUT
    except ImportError:   # Outside Blender (host tests): no team account.
        return False
    account = team_account_ui.account()
    return account is not None and account.view().status == SIGNED_OUT


def _app_states(now=None):
    """{app: (state, last result)} for ``CONNECTED_APPS`` as the AI Providers card reads them: ``"connected"`` (in
    the app registry), ``"found"`` (installed, so Connect can set it up) or ``"missing"`` (the card offers its setup
    to copy instead); the result is the card's last Connect/Disconnect line, ``(ok, template, fields)``, or None.
    Presentation only: actions re-admit their own connection state. A settled file witness may be reused until
    the next install poll; coarse/racy witnesses are read afresh without waiting on the UI thread."""
    try:
        import os
        import time
        from . import change_clock, external_setup, external_ui, ui
        from .external_clients import AppRegistry, DEFAULT_CLIENT
    except ImportError:   # Outside Blender (host tests): nothing is connected.
        return {app: ("found", None) for app in CONNECTED_APPS}
    now = time.monotonic() if now is None else now
    registry = AppRegistry(ui.state_directory())
    stat = None
    try:
        stat = os.stat(registry.path)
        key = change_clock.cache_key(stat, (str(registry.path), stat.st_dev, stat.st_ino,
                                           stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size), change=True)
    except OSError:
        key = (str(registry.path), None)
    polled = _registry_seen.get("when", -_INSTALL_CHECK)
    poll_due = now - polled >= _INSTALL_CHECK
    if _registry_seen.get("key") != key or poll_due:
        records = registry.load() if stat is not None else {}
        installed = {} if poll_due else dict(_registry_seen.get("installed", {}))
        states = {}
        for app in CONNECTED_APPS:
            if DEFAULT_CLIENT[app] in records:
                states[app] = "connected"
            else:
                if app not in installed:
                    installed[app] = external_setup.installed(app)
                states[app] = "found" if installed[app] else "missing"
        _registry_seen.update(key=key, when=now if poll_due else polled, installed=installed, states=states)
    return {app: (_registry_seen["states"][app], external_ui.results.get(app)) for app in CONNECTED_APPS}


def _app_row(app, state, result, windows):
    from .external_clients import APP_NAMES
    name = {"app": APP_NAMES[app]}
    failed = (result[1], result[2]) if result and not result[0] else None   # Why its settings were not changed.
    row_id, label = "connect_" + app, (CONNECT_APP, name)
    if windows:   # No named pipe yet: the card is off there, and the click gives the same reason.
        return leaf("apps_settings", row_id, label, NOT_ON_WINDOWS, glyph="apps",
                    operator="angelo.external_app_connect", properties=(("app", app),))
    if state == "connected":   # Disconnecting, and the setup to copy after a failure, are on its card.
        return leaf("apps_settings", row_id, (APP_CONNECTED, name), failed or APP_CONNECTED_WHY, glyph="apps",
                    operator="angelo.settings")
    if state == "missing":
        return leaf("apps_settings", row_id, label, APP_MISSING_WHY, glyph="apps", operator="angelo.settings")
    return leaf("apps_settings", row_id, label, failed or (CONNECT_APP_WHY, name), glyph="apps",
                operator="angelo.external_app_connect", properties=(("app", app),))


def _apps_settings_rows(chosen, fit_target, platform=None, states=None):
    """Connect Claude Code / Codex (or, once connected, where to disconnect), team sign-in while signed out, AI
    Providers (where each connection's details show), then the snapshot settings and their review."""
    import sys
    windows = (platform or sys.platform) == "win32"
    if states is None:   # Windows needs no state: its rows only say the connection is coming.
        states = {app: ("", None) for app in CONNECTED_APPS} if windows else _app_states()
    rows = [_app_row(app, *states[app], windows) for app in CONNECTED_APPS]
    if _team_sign_in_offered():
        rows.append(leaf("apps_settings", "team_sign_in", TEAM_SIGN_IN, TEAM_SIGN_IN_WHY, glyph="apps",
                         operator="angelo.team_sign_in"))
    rows.append(leaf("apps_settings", "ai_providers", PROVIDERS, PROVIDERS_WHY, glyph="apps",
                     operator="angelo.settings"))
    rows.append(leaf("apps_settings", "snapshot_settings", SNAPSHOT_SETTINGS, SNAPSHOT_SETTINGS_WHY,
                     operator="angelo.checkpoint_settings"))
    rows.append(leaf("apps_settings", "snapshot_review", SNAPSHOT_REVIEW, SNAPSHOT_REVIEW_WHY,
                     operator="angelo.checkpoint_retention"))
    return rows


def _goal_feature():
    """A goal Angelo holds across turns (goal_mode.rst): a chip in the composer, started by the message it goes with.
    Beside Attach (both are added to the message), so the grid gains no row in any language (chat_add_layout)."""
    return Feature("goal", n_("Goal", "Angelo"), n_("Keep going until it's done: measured after every turn"), glyph="goal",
                   order=1, section=ATTACH, action="popup_add_goal", placement="bar", lead_only=True,
                   owner="goal_loop")


def _apps_settings_feature():
    return Feature("apps_settings", APPS_SETTINGS, APPS_SETTINGS_WHY, glyph="apps", order=53, section=n_("Share"),
                   children=_apps_settings_rows, owner="chat_destinations")


def register_builtins():
    """The features this module, ``chosen_tools`` and ``goal_loop`` own (Blender-free); the rest register from their
    modules."""
    register(_attach_feature())
    register(_base_mesh_feature())
    register(_goal_feature())
    register(_hands_feature())
    register(_apps_settings_feature())


register_builtins()
