# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Blender side of the agent-script confinement (script_confinement, doc/angelo/script_confinement.rst).

* ``folders``: which folders are private (``private_roots``: the state folder, the account store, native CLI
  profiles, the worker, headless and scratch families) and which a script may read but never change (Blender's user
  config and the folders Blender and Angelo load code from: user scripts, add-ons, extensions, presets, the app's own).
* ``install_app``: installs the hook with them, and makes every place Blender keeps a callable for later run it
  confined when a confined thread put it there (``bpy.app.timers``, ``bpy.msgbus``, draw handlers, ``atexit``), and
  gates operators (``gate_operators``: programs, installs, preferences, grants and private paths).
* ``begin`` / ``end`` around each agent script (``BlenderExecutor``): what a script leaves in process-wide places
  without a call to judge is put back or confined at its end (``Sweep``): handlers and driver functions it added run
  confined, a text it set to run at load stops running at load, the import path, environment, builtins, interpreter
  hooks and Angelo's own modules go back to how they were, and a widening setting goes back.

Generic parts take their Blender objects as arguments, so the host tests drive them with stand-ins.
"""

import os
import sys

from . import script_confinement
from .script_confinement import GRANT, OPERATOR, ScriptConfined

# Operators a script may never run: they open another program, install code, save or reset preferences, or reload
# the add-ons (read_factory_settings, and read_homefile naming an app template: ``operator_refusal``).
DENIED_OPERATORS = frozenset({
    "wm.url_open", "wm.url_open_preset", "wm.path_open", "wm.save_userpref", "wm.read_userpref",
    "wm.read_factory_userpref", "wm.read_factory_settings", "wm.save_homefile", "wm.quit_blender", "wm.app_template_install",
    "render.play_rendered_anim", "file.external_operation", "image.external_edit", "script.reload",
    "script.python_file_run", "preferences.addon_install", "preferences.addon_enable", "preferences.addon_disable",
    "preferences.addon_remove", "preferences.addon_refresh", "preferences.app_template_install",
    "preferences.studiolight_install", "preferences.keyconfig_import", "preferences.theme_install",
    "preferences.script_directory_add", "preferences.script_directory_remove", "preferences.copy_prev",
    "preferences.reset_default_preferences",
})
DENIED_PREFIXES = ("extensions.",)
PATH_ARGUMENTS = frozenset({"filepath", "directory", "dirpath", "filename", "path", "files", "url", "file_path"})
FILE_OWNERS = {"text.save": "edit_text", "image.save": "edit_image"}   # Operators that write their data's own file.


def folders(state_directory, connection_directory, records=(), environ=None):
    """Angelo's private folders now, as file tools see them: (folders, families)."""
    from . import private_roots
    found = []
    try:
        found.extend(private_roots.private_roots(state_directory, connection_directory, records, environ))
    except (OSError, ValueError):
        found.extend([state_directory, connection_directory])
    found.extend(private_roots.registered())
    found.extend(private_roots.runtime_homes(environ))
    return [str(folder) for folder in found], [(str(parent), prefix) for parent, prefix in private_roots.families()]


def shared_libraries(local):
    """The app's own shared library folder, beside its version folder (where sitecustomize points DLL lookups): a DLL
    planted there loads at the next launch, and pxr adds it as a DLL folder while it imports on Windows."""
    if not local:
        return ""
    name = "blender.shared" if sys.platform == "win32" else "lib"   # sitecustomize's shared_lib_dirname.
    return os.path.join(os.path.dirname(os.path.normpath(local)), name)


def register_fixed_folders():
    """Blender's and the environment's folders Angelo's own code writes into, named for C before any script region
    began (after one, C refuses: a script could have chosen them): the cache's updates folder, a library folder the
    environment names. Each module registers its own as it resolves it."""
    for resolve in (_updates_folder, _library_folder):
        try:
            resolve()
        except Exception:   # noqa: BLE001 - not set up in this build or environment: nothing to register.
            pass


def _library_folder():
    from . import studio_state
    return studio_state.library_directory()


def _updates_folder():
    try:
        from . import update_ready
        folder = update_ready.directory()
    except Exception:   # noqa: BLE001 - no update support in this build: nothing staged to protect.
        return ""
    return str(folder) if folder else ""


def app_folders(bpy):
    """(private, write_only, families, trusted) for the running app."""
    from . import ui
    try:
        records = [record.to_dict() if hasattr(record, "to_dict") else record for record in ui.connections().records()]
    except Exception:   # noqa: BLE001 - no connection list yet: the folders alone.
        records = []
    private, families = folders(ui.state_directory(), ui.connection_directory(), records)
    resource = bpy.utils.resource_path
    user = bpy.utils.user_resource
    write_only = [user("CONFIG"), user("SCRIPTS"), user("EXTENSIONS"), user("DATAFILES"), resource("LOCAL"),
                  resource("SYSTEM"), sys.prefix, sys.base_prefix, os.path.dirname(os.path.dirname(__file__)),
                  shared_libraries(resource("LOCAL"))]
    try:
        write_only.extend(directory.directory for directory in bpy.context.preferences.filepaths.script_directories)
    except (AttributeError, TypeError):
        pass
    trusted = list(write_only)
    trusted.extend(path for path in sys.path if path and os.path.isabs(path) and path.startswith(
        (sys.prefix, sys.base_prefix, resource("LOCAL"))))
    # The bytecode cache (``PYTHONPYCACHEPREFIX``, which a release sets): what Python loads for installed modules,
    # so scripts can't change any of it. Only its mirrors of the installed folders hold installed bytecode, accepted
    # byte for byte as beside its source; the rest mirrors other sources (a document's folder, a script's), whose
    # bytecode is not installed code (break-it-confine-reparse SHOULD-5). A confined import's own cache write is
    # refused there and skipped by importlib.
    if sys.pycache_prefix:
        write_only.append(sys.pycache_prefix)
        trusted.extend(pycache_mirrors(trusted))
    write_only.extend(os_startup())   # Never trusted: what is there is the person's, not installed code.
    # Never trusted either: what Restart to update runs is staged there (the helper script sh/cmd read as they run,
    # the Windows installer, ready.json), so a script that wrote there would choose what the next update runs
    # (p10c-1-update).
    write_only.append(_updates_folder())
    return private, [path for path in write_only if path], families, [path for path in trusted if path]


def pycache_mirrors(folders):
    """Where the bytecode cache (``sys.pycache_prefix``) keeps the bytecode of sources inside ``folders``: each
    folder's mirror, named by importlib's own rule (``cache_from_source``: the source's absolute folder below the
    prefix, a Windows drive dropped), for the folder as written and as resolved. Nothing without a prefix."""
    import importlib.util
    if not sys.pycache_prefix or sys.implementation.cache_tag is None:
        return []
    found = []
    for folder in folders:
        if not folder or not os.path.isabs(folder):
            continue   # Not a folder (an unset resource), or one whose mirror would follow the working folder.
        for spelling in dict.fromkeys((folder, os.path.realpath(folder))):
            mirror = os.path.dirname(importlib.util.cache_from_source(os.path.join(spelling, "mirror.py")))
            if mirror not in found:
                found.append(mirror)
    return found


def os_startup(environ=None, platform=None, home=None):
    """What the operating system or a shell runs later on its own, so a script that writes there starts a program
    outside its confinement: login items, autostart entries, shell startup files and the user's Python
    site-packages (``usercustomize`` and ``.pth`` files run in every Python). Also the audio stack's per-user
    configuration, read when Angelo opens a sound device: an ALSA ``~/.asoundrc`` can load a library or pipe to a
    command, OpenAL Soft's can redirect the stream to any file, PulseAudio's and PipeWire's can connect out or load
    modules, JACK's ``~/.jackdrc`` names a server to start (BREAKIT break-it-confine-fixes-b31e34840ca MUST-3), with
    the other places the same libraries read (alsa-lib's ``$XDG_CONFIG_HOME/alsa/asoundrc``, libpulse's legacy
    ``~/.pulse``; review-confine-fixes-3cb71ae002d S-4). Read, never changed. The list names the places these
    libraries read today; an environment variable pointing one elsewhere is set before Angelo starts, by the person.
    A library's places are the same on every platform outside Windows, resolved the same way (``$XDG_CONFIG_HOME``,
    else ``~/.config``): OpenAL Soft reads ``$XDG_CONFIG_HOME/alsoft.conf`` on macOS as on Linux (its
    ``alconfig.cpp``), and macOS once listed only the libraries' home-folder files (iso-mac's macOS run of
    7181095e954's suite: a write to ``$XDG_CONFIG_HOME/alsoft.conf`` went through)."""
    environ = os.environ if environ is None else environ
    platform = platform or sys.platform
    home = home or os.path.expanduser("~")
    found = [os.path.join(home, name) for name in (".bashrc", ".bash_profile", ".bash_login", ".profile", ".zshrc",
                                                    ".zprofile", ".zshenv", ".zlogin", ".config/fish")]
    if platform != "win32":
        config = environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        found.extend(os.path.join(home, name) for name in (".asoundrc", ".asoundrc.asoundconf", ".alsoftrc",
                                                           ".jackdrc", ".pulse"))
        # Each name written out: the grant inventory classifies the names path arguments spell (alsa, pulse, ...).
        found.extend([os.path.join(config, "alsoft.conf"), os.path.join(config, "pulse"),
                      os.path.join(config, "pipewire"), os.path.join(config, "alsa")])
    if platform == "darwin":
        found.append(os.path.join(home, "Library", "LaunchAgents"))
        found.append(os.path.join(home, "Library", "Preferences", "alsoft.conf"))
    elif platform == "win32":
        appdata = environ.get("APPDATA")
        if appdata:
            found.append(os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"))
            found.append(os.path.join(appdata, "alsoft.ini"))
    else:
        found.extend([os.path.join(config, "autostart"), os.path.join(config, "systemd", "user")])
    try:
        import site
        found.append(site.getusersitepackages())
    except (ImportError, AttributeError):
        pass
    return found


def tool_output_folders(bpy):
    """Folders a script reads but only Angelo's tools write (outside the confinement): the downloads of an unsaved
    document (``download_folders.unsaved_base``) and the settings import's backup, whose Undo puts files back into
    startup folders (``settings_import_apply.backup_folder``). Never trusted: a downloaded ``.py`` is not installed
    code, so neither its functions nor what it leaves behind count as Angelo's. Never a shorter list: a module that
    fails to import or a folder that cannot be found fails registration, never leaves a protected folder writable
    (013c161cf3e returned without the downloads when the import failed; Devin audit D2)."""
    from . import download_folders, settings_import_apply
    return [str(settings_import_apply.backup_folder()), str(download_folders.unsaved_base(bpy))]


def _bytecode_loaders():
    """The installed loaders that may unmarshal a .pyc while a script runs (the bytes are checked against the file,
    in C): importlib's own and the shipped-file loader of ``scripts/site/sitecustomize.py``."""
    import importlib._bootstrap_external as external
    found = [external._compile_bytecode.__code__]
    for module in list(sys.modules.values()):
        function = getattr(getattr(getattr(module, "__loader__", None), "get_code", None), "__func__", None)
        code = getattr(function, "__code__", None)
        if code is not None and os.path.basename(code.co_filename) == "sitecustomize.py":
            found.append(code)
            break
    return found


def install_app(bpy):
    """Install the confinement for this app, before any agent script (``__init__.register``, the worker and headless
    mains). Later calls add the folders known now (a connection added since, a worker's folder)."""
    guard = script_confinement._guard
    from . import net_integrity   # noqa: F401 - what Angelo's requests are built from, captured before any script.
    if getattr(guard, "DIAG_NO_INSTALL", 0):
        return False   # A diagnostic build without the confinement (never released: test_native_guard_diag_define).
    private, write_only, families, trusted = app_folders(bpy)
    if guard is not None:   # The C confinement: it gates operators and registration points itself.
        guard.add_roots(private, write_only, families, trusted, tool_output_folders(bpy))
        from . import owned_paths, ui
        owned_paths.name_store(os.path.join(ui.state_directory(), "writable-paths"))   # Vouched paths, kept by C.
        if not _native_installed:
            _native_installed.append(True)
            guard.install_wrappers()
            from .script_prepare import Prepared
            guard.set_compiler(Prepared.compiled)   # The only compiler run_confined accepts (by identity, in C).
            guard.add_bytecode_loaders(_bytecode_loaders())
            install_sweeps(bpy)
            unshared_semaphores(guard.confined)
        from . import grant_guard   # Grant operators refused at the operator call in C while confined.
        guard.add_denied_operators(sorted(set(grant_guard.REGISTRY) | grant_guard.ACCOUNT_OPERATORS))
        return len(_native_installed) == 1
    first = script_confinement.install(private=private, write_only=write_only, families=families, trusted=trusted,
                                       tool_output=tool_output_folders(bpy))
    if first:
        defer_registrars(bpy)
        gate_operators(bpy)
        install_sweeps(bpy)
    return first


def unshared_semaphores(confined):
    """A semaphore a confined thread makes (a thread pool's lock: ``multiprocessing.dummy``, ``ThreadPool``) is
    unlinked at once, as under the fork start method, so it never registers with multiprocessing's resource tracker,
    a helper process that a confined thread may not start (macOS, where the default start method is spawn). It can't
    be shared with another process then, and no confined thread can start one anyway (a process pool stays refused)."""
    try:
        import _multiprocessing
    except ImportError:
        return
    base = _multiprocessing.SemLock
    if getattr(base, "_angelo_unshared", False):
        return

    class SemLock(base):
        _angelo_unshared = True

        def __new__(cls, kind, value, maxvalue, name, unlink):
            return base.__new__(cls, kind, value, maxvalue, name, True if confined() else unlink)

    SemLock.__module__, SemLock.__qualname__ = base.__module__, base.__qualname__
    _multiprocessing.SemLock = SemLock
    guard = script_confinement._guard
    if guard is not None and hasattr(guard, "note_replaced_module"):
        guard.note_replaced_module("_multiprocessing")   # A second _multiprocessing would hold the original class.


_native_installed = []   # Once per process (the C side refuses a second set of sweeps while confined anyway).


def install_sweeps(bpy, package=None):
    """The hook's start and end sweeps (script_confinement_sweep), built now from functions bound now."""
    from . import grant_guard, script_confinement_sweep
    guard = script_confinement._guard
    if guard is not None:
        agent_code, defer, set_sweep = guard.is_agent_code, guard.defer, guard.set_sweep
        installed_file = _installed_file_function(trusted_folders(bpy))
    else:
        hook = script_confinement._hook
        agent_code, defer, set_sweep = hook["agent_code"], hook["defer"], hook["set_sweep"]
        installed_file = hook["installed_file"]

    def is_agent_function(value):
        code = getattr(value, "__code__", None)
        return code is not None and agent_code(code)

    def preferences():
        addon = bpy.context.preferences.addons.get(__package__)
        return getattr(addon, "preferences", None) if addon is not None else None

    if guard is not None:   # Namespaces are journaled in C; the Python sweep covers the rest.
        from .script_confinement_sweep import STANDARD
        guard.watch_modules([package or __package__] + list(STANDARD))
        if hasattr(guard, "angelo_package"):
            guard.angelo_package(package or __package__)   # Angelo's frames: what a script's can't read (opaque.cc).
        set_sweep(*script_confinement_sweep.native_python_sweep(package or __package__, installed_file))
    else:   # Without the native guard; with it, logging is put back in C at every region's end (logging.cc).
        set_sweep(*script_confinement_sweep.python_sweep(package or __package__, installed_file))
        from .script_confinement_logging import logging_sweep
        set_sweep(*logging_sweep(package or __package__))   # Angelo's loggers and the root logger, as found.
    from .strict_protection import NAME as STRICT, sync as strict_sync   # On protects (reviews of 93ef5b1, a121c9a).
    from .strict_flag import enabled as strict_enabled   # C's flag: one atomic load, never RNA (the coordinator's (Q)).
    set_sweep(*script_confinement_sweep.blender_sweep(
        bpy, defer, is_agent_function, grant_guard.WIDENING, getattr(grant_guard, "_widens", None), preferences,
        package or __package__, protective=(STRICT,), resync=strict_sync, protecting_now=strict_enabled))


def trusted_folders(bpy):
    return app_folders(bpy)[3]


def _installed_file_function(folders):
    """Whether a file is installed code (the sweep keeps import-path entries and finders that are), by the folders
    as resolved now; bound at install like the rest of the sweep."""
    import os
    roots = tuple(os.path.normcase(os.path.realpath(folder)) + os.sep for folder in folders if folder)
    realpath, normcase, isinstance_, str_, any_ = os.path.realpath, os.path.normcase, isinstance, str, any

    def installed_file(name):
        if not isinstance_(name, str_):
            return False
        if name.startswith("<frozen "):
            return True
        if name.startswith("<"):
            return False
        try:
            path = normcase(realpath(name))
        except (OSError, ValueError):
            return False
        return any_(path.startswith(root) for root in roots)
    return installed_file


def deferring(original, position=0, keyword=None):
    """``original`` (a function that keeps a callable to call later), with that callable confined when a confined
    thread passes it (``keyword`` names it, else it is positional argument ``position``)."""
    if getattr(original, "_angelo_confined", False):
        return original
    defer, is_confined = script_confinement._hook["defer"], script_confinement._hook["confined"]
    kept = {}   # id(callable) -> (callable, its confined form): unregister and is_registered find what was registered.

    def call(*args, **kwargs):
        if is_confined():
            if keyword is not None and keyword in kwargs:
                function = kwargs[keyword]
                kwargs[keyword] = kept.setdefault(id(function), (function, defer(function)))[1]
            elif len(args) > position:
                function = args[position]
                args = args[:position] + (kept.setdefault(id(function), (function, defer(function)))[1],) \
                    + args[position + 1:]
        return original(*args, **kwargs)
    call._angelo_confined, call._angelo_kept, call.__doc__ = True, kept, getattr(original, "__doc__", None)
    return call


def finding(original, kept, position=0):
    """``unregister`` / ``is_registered`` of a deferring registrar: the confined form registered for a callable."""
    def call(*args, **kwargs):
        if len(args) > position and id(args[position]) in kept:
            args = args[:position] + (kept[id(args[position])][1],) + args[position + 1:]
        return original(*args, **kwargs)
    call._angelo_confined = True
    return call


def defer_registrars(bpy):
    """Every Blender place that keeps a callable for later: what a confined thread puts there runs confined."""
    import atexit
    timers = bpy.app.timers
    register = deferring(timers.register, 0, "function")
    for name, original in (("unregister", timers.unregister), ("is_registered", timers.is_registered)):
        _replace(timers, name, finding(original, register._angelo_kept))
    _replace(timers, "register", register)
    _replace(bpy.msgbus, "subscribe_rna", deferring(bpy.msgbus.subscribe_rna, 3, "notify"))
    _replace(atexit, "register", deferring(atexit.register, 0))
    for name in dir(bpy.types):
        cls = getattr(bpy.types, name, None)
        for method in ("draw_handler_add", "draw_cursor_add"):
            original = cls.__dict__.get(method) if isinstance(cls, type) else None
            if original is not None and not getattr(original, "_angelo_confined", False):
                _replace(cls, method, deferring_class_method(original))


def deferring_class_method(original):
    """A class method that keeps a callable (``Space.draw_handler_add``, defined once on ``Space`` and called on
    each space type), with that callable confined when a confined thread passes it; still bound to the class it is
    called on."""
    defer, is_confined = script_confinement._hook["defer"], script_confinement._hook["confined"]

    def call(cls, function, *args, **kwargs):
        if is_confined():
            function = defer(function)
        return original.__get__(None, cls)(function, *args, **kwargs)
    call._angelo_confined, call.__doc__ = True, getattr(original, "__doc__", None)
    wrapped = classmethod(call)
    return wrapped


def _replace(owner, name, value):
    try:
        setattr(owner, name, value)
        return True
    except (AttributeError, TypeError):
        return False


def _account_operators():
    from .grant_guard import ACCOUNT_OPERATORS   # Refused to a script as the grant operators are (grant_guard).
    return ACCOUNT_OPERATORS


def operator_refusal(idname, kwargs, context_file=None, registry=()):
    """Why a confined script may not run operator ``idname`` with ``kwargs`` (None: it may). ``context_file``: the
    file an operator without a path writes (``FILE_OWNERS``); ``registry``: the grant operators (grant_guard)."""
    if idname in registry or idname in _account_operators():
        return GRANT
    if idname in DENIED_OPERATORS or idname.startswith(DENIED_PREFIXES):
        return OPERATOR
    if idname == "wm.read_homefile" and "app_template" in kwargs:
        return OPERATOR
    check = script_confinement._hook["check_path"]
    for key, value in kwargs.items():
        if key in PATH_ARGUMENTS:
            for item in (value if isinstance(value, (list, tuple)) else [value]):
                path = item.get("name") if isinstance(item, dict) else item
                if isinstance(path, str) and path and not path.startswith(("http:", "https:", "mailto:")):
                    check(_absolute(path), "w")
    if context_file:
        check(_absolute(context_file), "w")
    return None


def _absolute(path):
    if path.startswith("//"):
        try:
            import bpy
            return bpy.path.abspath(path)
        except (ImportError, AttributeError):
            return path[2:]
    return path.removeprefix("file://")


def gate_operators(bpy):
    """``bpy.ops.<module>.<name>(...)`` and ``_bpy.ops.create_function``: a confined script's operator call is
    refused for ``operator_refusal``'s reasons, from code no script can rebind (both references are replaced)."""
    import _bpy
    from . import grant_guard
    create = _bpy.ops.create_function
    if getattr(create, "_angelo_confined", False):
        return
    is_confined, armed = script_confinement._hook["confined"], script_confinement._hook["armed"]
    registry = grant_guard.REGISTRY

    class Operator:
        __slots__ = ("_operator", "_idname")

        def __init__(self, operator, idname):
            self._operator, self._idname = operator, idname

        def __call__(self, *args, **kwargs):
            if armed() and is_confined():
                owner = FILE_OWNERS.get(self._idname)
                data = getattr(bpy.context, owner, None) if owner else None
                reason = operator_refusal(self._idname, kwargs, getattr(data, "filepath", None) or None, registry)
                if reason is not None:
                    raise ScriptConfined(reason, self._idname)
            return self._operator(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._operator, name)

        def __repr__(self):
            return repr(self._operator)

        def __str__(self):
            return str(self._operator)

    def create_function(module, function):
        return Operator(create(module, function), module + "." + function)
    create_function._angelo_confined = True
    _bpy.ops.create_function = create_function
    import bpy.ops as ops_module
    ops_module._op_create_function = create_function
