# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Start-up of the headless child inside ``Angelo --background`` (doc/angelo/external_mcp.rst, "Headless").

The MCP launcher starts it as::

    <Angelo> --background --disable-autoexec --offline-mode --python-exit-code 1
             --python <scripts>/angelo/angelo_headless.py -- --in FD --out FD --file PATH
             --client NAME --app KIND --levels read,edit --preferences yours

with ``ANGELO_STATE_DIR`` and ``ANGELO_CONNECTIONS_DIR`` in a private temporary folder the launcher made (and
removes after the child exits), ``ANGELO_HEADLESS=1`` and ``BLENDER_USER_CONFIG`` at the private copy of the person's
preferences (``headless_preferences``; with factory settings instead, ``--factory-startup`` and no copy). It
turns preference saving off, registers only
what an app's calls use (the approval and commit operators, the program handlers, document and scene tracking; no
chat, sign-in, provider, catalog or timer), loads the file's data with ``load_ui=False, use_scripts=False`` into the
background window, says ``ready`` on its pipe and serves until the pipe closes or the session is idle.
"""

import argparse
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path

from . import headless_files
from . import headless_preferences
from .external_cli_protocol import valid_label
from .external_clients import APPS, LEVELS

PRIVATE_PREFIX = "angelo-headless-"   # headless_session names the launcher's private folders so.
UNDO_STEPS = 8   # Undo steps a background process keeps: enough for a commit's own step, bounded memory.


def parse(argv):
    parser = argparse.ArgumentParser(prog="angelo-headless")
    parser.add_argument("--in", dest="read_fd", type=int, required=True)
    parser.add_argument("--out", dest="write_fd", type=int, required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--app", required=True, choices=APPS)
    parser.add_argument("--levels", required=True)
    parser.add_argument("--idle", type=float, default=None)
    parser.add_argument("--resume", default=None, help="this session's output file to continue from")
    parser.add_argument("--approval", default=None, choices=("AUTO_BOUNDED", "ASK", "READ_ONLY"))
    parser.add_argument("--private", default=None, help="the private folder the launcher made for this child")
    parser.add_argument("--organization", default=None,
                        help="the signed-in organization's app policy as JSON (the launcher read it for this child)")
    parser.add_argument("--approvals", default=None,
                        help="the person's state folder, whose script file approvals this child reads (never writes)")
    parser.add_argument("--source-identity", default=None,
                        help="the source's identity as the launcher read it before starting this child (JSON)")
    parser.add_argument("--working", default=None, help="the working copy the launcher made next to --file")
    parser.add_argument("--preferences", default=None,
                        help="'yours' (the person's preferences, copied into --private) or why factory settings")
    options = parser.parse_args(argv)
    if not valid_label(options.client):
        parser.error("--client must be 1-32 characters from [A-Za-z0-9._-]")
    # The app's levels as connected; tools_for(headless=True) keeps only what runs without a window.
    options.levels = tuple(level for level in LEVELS if level in options.levels.split(","))
    options.organization = organization_policy(options.organization)
    options.source_identity = _identity(options.source_identity)
    return options


def _identity(text):
    """``--source-identity`` as ``headless_files.identity`` returns it, or None."""
    try:
        value = json.loads(text) if text else None
    except ValueError:
        value = None
    return tuple(value) if isinstance(value, list) and all(isinstance(part, int) for part in value) else None


def organization_policy(text):
    """The ``--organization`` JSON as a policy dict; anything else the launcher could not have sent counts as the
    strictest policy (never as none)."""
    from .team_org_policy import STRICTEST
    from .team_view import org_policy
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    return org_policy(value) if isinstance(value, dict) else org_policy(STRICTEST)


def _isolated(environ, private):
    """The private folder the launcher made for this child (its state is inside); refuse to run elsewhere.

    The child removes it when it exits, so a launcher that is killed leaves nothing behind; it must be a real
    owner-only directory named as the launcher names them, holding ``ANGELO_STATE_DIR``.
    """
    state = environ.get("ANGELO_STATE_DIR")
    if not state or environ.get("ANGELO_HEADLESS") != "1" or not private:
        raise RuntimeError("The headless child runs only as the MCP launcher starts it")
    private = Path(private)
    info = os.lstat(private)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            or not private.name.startswith(PRIVATE_PREFIX) or Path(state) != private / "state"):
        raise RuntimeError("The headless child's private folder is not one the launcher made")
    (private / "child.pid").write_text(str(os.getpid()))
    return private


class HeadlessFailure(RuntimeError):
    """A start-up failure with the code the launcher acts on (``headless_preferences.NOT_READABLE``: start again
    with factory settings)."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


WORKING_COPY = ("The open file is {name}. Blender works on a copy next to it ({copy}) so that nothing, not even an "
                "add-on, can save {name}; the copy becomes this session's output with the first saved edit and is "
                "removed otherwise.")
SOURCE_CHANGED_AT_OPEN = ("stale: {name} changed on disk while Angelo opened it (one of your add-ons or another app "
                          "wrote it), so this session stopped. Call angelo_open_file again to load it as it is now.")
APPLY_OPERATORS = ("angelo.apply_approved_tool", "angelo.apply_approved_script")


def apply_operators(ui):
    """The window's own classes for applying an approved tool and an approved script (``ui.CLASSES``): the headless
    child dispatches through the same operators, so a script file the person approved runs here exactly as there
    (break-it review H1: the script one was missing, so every approved run failed in a real child)."""
    found = {getattr(cls, "bl_idname", ""): cls for cls in ui.CLASSES}
    return tuple(found[name] for name in APPLY_OPERATORS)


def _register(bpy, ui):
    """Only what an app's calls use; the add-on itself (chat, sign-in, timers) is never enabled here."""
    from . import agent_workspace_ui, native_programs
    from .blender_tools import track_scene_changes
    from .program_storage import retire_native_residue_after_load
    for cls in (ui.ANGELO_Preferences, ui.ANGELO_OT_approve) + apply_operators(ui):
        bpy.utils.register_class(cls)
    if ui.__package__ not in bpy.context.preferences.addons:
        bpy.context.preferences.addons.new().module = ui.__package__
    agent_workspace_ui.register()
    native_programs.register()

    @bpy.app.handlers.persistent
    def after_load(_):
        retire_native_residue_after_load(ui.document_key())
    bpy.app.handlers.load_pre.append(ui.document_load_pre)
    bpy.app.handlers.load_post.append(after_load)
    bpy.app.handlers.depsgraph_update_post.append(track_scene_changes)


def _prepare_preferences(bpy):
    """Before the first load (parallel-agent workers share it): no thumbnails; Undo and saved state as in a window."""
    from .program_undo import keep_background_document
    paths = bpy.context.preferences.filepaths
    paths.file_preview_type = 'NONE'   # No thumbnail render on save.
    edit = bpy.context.preferences.edit
    # A native commit needs Global Undo and at least one step; nothing here undoes past a reload, and the person's
    # count (0 refuses every commit, up to 256 holds that many whole-file steps) is theirs to set in the window. The
    # preferences are protected, so neither is ever saved.
    edit.use_global_undo, edit.undo_steps = True, UNDO_STEPS
    # Every load this process makes (the working copy, --resume, each reload) then starts that document's own Undo
    # history and reads as saved, as a window's load does, and so does every save: native commits (programs,
    # recipes) need its active step, and angelo_blend_file's unsaved_changes is then true only for unsaved edits.
    if not keep_background_document():
        print("Angelo headless: this Angelo keeps no Undo history in background, so staged programs and recipes "
              "cannot apply", file=sys.stderr)


def main(argv, environ=None):
    environ = os.environ if environ is None else environ
    options = parse(argv)
    private = _isolated(environ, options.private)
    if options.organization:   # managed_policy.current() applies it, the stricter value winning, as in the window.
        from . import managed_policy
        organization = options.organization
        managed_policy.set_organization_source(lambda: organization)
    from .headless_pipe import PipeServer
    import bpy
    try:
        _serve(bpy, options, PipeServer)
    finally:
        shutil.rmtree(private, ignore_errors=True)


def _serve(bpy, options, server_type):
    from . import ui
    from .headless_host import HeadlessEnvironment, HeadlessHost
    host = None

    def closed():
        if host is not None:
            host.end()
    channel = server_type(options.read_fd, options.write_fd, None, None, on_closed=closed)
    preferences = source = resume = working = None
    try:
        headless_preferences.protect(bpy)   # First: nothing below can save preferences (read-only).
        if (options.preferences == headless_preferences.YOURS
                and not headless_preferences.copy_readable(bpy, options.private)):
            raise HeadlessFailure(headless_preferences.NOT_READABLE, "Blender could not read the preferences")
        source = headless_files.check_source(options.file)
        resume = headless_files.check_resume(source, options.resume) if options.resume else None
        _prepare_preferences(bpy)
        if options.preferences == headless_preferences.YOURS:
            # Counted before Angelo registers its own classes; an add-on that did not load is named, never fatal.
            preferences = headless_preferences.describe(options.preferences,
                                                        *headless_preferences.addons(bpy, ui.__package__))
        else:
            preferences = headless_preferences.describe(options.preferences)
        _register(bpy, ui)
        if resume is not None:   # This session's own output: Blender keeps it open and saves go there.
            result = bpy.ops.wm.open_mainfile(filepath=str(resume), load_ui=False, use_scripts=False)
            if "FINISHED" not in result:
                raise RuntimeError("Blender could not open the file")
            working = resume
        else:   # Never the source itself: a copy next to it (headless_working_copy).
            from .headless_working_copy import open_copy
            working = open_copy(bpy, source, options.working)
        if options.source_identity is not None and headless_files.identity(source) != options.source_identity:
            raise HeadlessFailure("stale", SOURCE_CHANGED_AT_OPEN.format(name=source.name))
        from . import live_windows
        if live_windows.first(bpy.context.window_manager) is None:
            raise RuntimeError("No background window to work in")
        try:
            import gpu
            gpu.init()   # Views draw offscreen through the background window's 3D Viewport.
        except (ImportError, SystemError) as error:
            print("Angelo headless: views are unavailable:", error, file=sys.stderr)
        if options.approvals and "python" in options.levels:
            from . import script_files
            script_files.attach(options.approvals, read_only=True)
        environment = HeadlessEnvironment(ui, options.approval, source=source, levels=options.levels)
        host = HeadlessHost(environment, source=source, label=options.client, app=options.app, levels=options.levels,
                            bpy=bpy, source_identity=options.source_identity, working=working)
        environment.host = host

        @bpy.app.handlers.persistent
        def after_save(_):   # Angelo's own: an in-process save of the open copy is the session's own state.
            host.track_in_process_save()
        bpy.app.handlers.save_post.append(after_save)
        from . import blend_file   # Reads name the file the app opened, never the working copy (review L3).
        blend_file.shown_path = lambda path: str(source) if host.session_file_open() else path
        if resume is not None:   # Edits so far are in this session's output; it keeps saving there.
            host.output, host.output_identity = resume, headless_files.identity(resume)
        direct = working == source   # A read-only folder: Blender has the source itself open.
        from .headless_session import WORKING_ON_SOURCE
        host.file_note = " ".join((WORKING_ON_SOURCE if direct else WORKING_COPY.format(name=source.name, copy=Path(
            working).name), preferences["note"] + "."))
    except Exception as error:  # noqa: BLE001 - the launcher reads why, then the child exits.
        if working is not None and working not in (source, resume):   # Never leaves a copy it made behind.
            from .headless_working_copy import discard
            discard(working, source, headless_files.identity(working))
        channel.ready({"ok": False, "code": getattr(error, "code", "unavailable"), "error": str(error)})
        return
    channel.handler, channel.client = host.handle_request, host.client
    channel.on_request = lambda: setattr(host, "last_call", time.monotonic())
    host.channel = channel
    host.start()
    channel.start()
    channel.ready({"ok": True, "pid": os.getpid(), "blender": bpy.app.version_string, "file": str(source),
                   "levels": list(options.levels), "writable": headless_files.folder_writable(source),
                   "preferences": preferences, "working_on_source": direct,
                   "working": None if direct else str(working),
                   "working_identity": None if direct else list(host.working_identity or ())})
    if options.idle is not None:
        host.run(ui, idle_exit=options.idle)
    else:
        host.run(ui)
