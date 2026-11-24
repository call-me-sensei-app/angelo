# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's agent runtime: the patched Codex build Angelo runs in place of the person's own Codex.

The build adds one experimental app-server parameter, ``cacheAffinityKey``, on
``thread/start``, ``thread/resume`` and ``thread/fork``: a stable prompt-cache
routing key, so a new chat's first request reads the opening an earlier chat
with the same startup context cached (``cache_affinity``; doc/angelo/gpt_launch.rst,
"Agent runtime and prompt caching"). Angelo returns to stock Codex once
upstream ships the feature (openai/codex#21796). ``agent_runtime.json`` pins the
build: its version, its fork commit, its features and the files each platform
bundles.

Selection (``cli_runtime.find_executable("codex")`` asks here first):

1. ``ANGELO_AGENT_RUNTIME`` (that spelling only: the variable is newer than
   the rename): the path of the build's ``codex`` executable (a development
   build that bundles none), or ``off`` for the person's own Codex even where
   the app bundles one (the native suites' stand-in CLIs). A set but empty
   value, or a path that is not an executable file, is an error, never a silent
   fall back, so a measurement cannot run on the wrong runtime unnoticed. A
   release build (``release_build``: its installer carries the pin's
   ``release_marker``, written only with ``ANGELO_REQUIRE_AGENT_RUNTIME``)
   accepts only ``off``: a path is refused, so nobody can slip another Codex
   under a shipped app's sign-in. ``off`` in a release is the person's explicit
   choice of their own Codex, whose own settings then apply (the one exception
   to "never the person's config"; the chat says so: ``own_codex_row``).
2. The bundled runtime: ``Angelo.app/Contents/Helpers/codex`` on macOS,
   ``<install folder>\\agent-runtime\\codex.exe`` on Windows,
   ``<extracted folder>/agent-runtime/codex`` on Linux, beside its
   ``codex-code-mode-host`` (Codex finds that next to its own executable).
3. Otherwise none: the person's own Codex, found as before (development builds). A release build whose runtime is
   missing refuses instead (``MISSING``).

Every process of the agent runtime is verified (``verify``): it must report the
pinned version, and its code-mode host must be beside it. A shared app-server
is also asked whether it accepts the key (``cache_affinity.check``), since
the build reports the same version as stock Codex. ``kind`` says which Codex ran
a request (``bundled``, ``override`` or ``own``) for its receipt.

Standard library only; ``bpy`` is read only when Blender runs this module.
"""

import functools
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import product_env
from .runtime_listing import (CATALOG_PROVIDER, DEFAULT_WIRE, LISTING_PREFIX, catalog_listing,  # noqa: F401
                              listing_home, valid_wire, wire_listings)
from .protocol import RpcError
from .ui_text import n_

OVERRIDE = "AGENT_RUNTIME"  # ANGELO_AGENT_RUNTIME (product_env)
OFF = "off"
PIN = json.loads((Path(__file__).resolve().parent / "agent_runtime.json").read_text(encoding="utf-8"))
_VERSION = re.compile(r"/(\d+\.\d+\.\d+[^\s(;]*)")


# ``codex app-server`` without the person's own ``$CODEX_HOME/config.toml``; sign-in still uses CODEX_HOME (the fork's
# flag, the same as ``codex exec --ignore-user-config``). Nothing of an Angelo thread goes where the person's own Codex
# config points, and their config never changes Angelo's agent (coordinator rulings, 2026-11-20). A pin that does not
# list it in ``app_server_flags`` names a build without it: Angelo refuses to start that runtime, never falling back to
# loading the person's config, and the release checks refuse to ship that pin (doc/angelo/releasing.rst).
IGNORE_USER_CONFIG = "--ignore-user-config"
# What a person reads when Angelo refuses to start its runtime (translated: display_text.Text).
NO_FLAG = n_("This build's agent runtime (Codex {version}) can't keep your own Codex settings out of Angelo's chats, "
             "so Angelo doesn't start it. Reinstall Angelo.")
REFUSED_START = n_("Angelo starts its agent runtime only in the ways it was built for, and never with your own Codex "
                   "settings. Reinstall Angelo if this keeps happening.")
MISSING = n_("Angelo's agent runtime is missing. Reinstall Angelo.")
EMPTY_OVERRIDE = n_("ANGELO_AGENT_RUNTIME is set but empty. Point it at the agent runtime's codex executable, set it "
                    "to {off} to use your own Codex, or unset it.")
RELEASE_OVERRIDE = n_("This release of Angelo runs only its own agent runtime, so ANGELO_AGENT_RUNTIME cannot name "
                      "another Codex ({value}). Unset it, or set it to {off} to use your own Codex.")
NOT_EXECUTABLE = n_("ANGELO_AGENT_RUNTIME names {value}, which is not an executable file. Point it at the agent "
                    "runtime's codex executable, set it to {off} to use your own Codex, or unset it.")
WRONG_VERSION = n_("Angelo's agent runtime ({path}) reports Codex {found}, but this version of Angelo runs {expected}. "
                   "Reinstall Angelo, or point ANGELO_AGENT_RUNTIME at the pinned build.")
UNREADABLE_VERSION = n_("an unreadable version")
INCOMPLETE = n_("Angelo's agent runtime is incomplete: {name} is missing beside {path}. Reinstall Angelo.")
# The command lines Angelo starts its runtime with, and no other (re-review of ffa4cb56717, SHOULD-1): the app-server
# with the flag first and only ``--listen``/``-c`` options after it; ``login`` (``status``, ``--with-api-key``) with at
# most the credential-store root option; the model-catalog listings ``catalog_listing`` builds (``runtime_listing``: a
# wire's one only from a listing folder of Angelo's own, ``listing_home``); ``--version``. Any other root option before
# a subcommand, ``exec``/``review``/``resume``, the terminal UI and a command-line string would all load the person's
# config.
APP_SERVER_OPTIONS = ("--listen", "-c")
LISTEN = "stdio://"
LOGIN_TAILS = ((), ("status",), ("--with-api-key",))
LOGIN_ROOT_OPTION = "cli_auth_credentials_store="
FIXED_COMMANDS = (("--version",), ("debug", "models", "--bundled"))


# ``ANGELO_AGENT_RUNTIME=off`` in a release build: the person's explicit choice of their own Codex, whose own settings
# then apply (re-review of ffa4cb56717, SHOULD-3, ruled: the one exception to "never the person's config").
OWN_CODEX = n_("This chat uses your own Codex (ANGELO_AGENT_RUNTIME=off) with its own settings, not Angelo's agent "
               "runtime.")


def own_codex_row(kind_, state):
    """The chat row saying ``OWN_CODEX`` (a system notice, translated where the chat draws it), or None: a release
    build running the person's own Codex, once per chat (``state["own_codex_noted"]``, the chat's saved state)."""
    chose_own = (override() or "").strip().lower() == OFF
    if kind_ != "own" or state.get("own_codex_noted") or not chose_own or not release_build():
        return None
    state["own_codex_noted"] = True
    from .ui_text import notice
    return {"role": "system", **notice(OWN_CODEX)}


def _refused(template, **fields):
    from .display_text import Text
    return RpcError(Text(template, **fields))


def require_user_config_ignored():
    """``RpcError`` unless the pinned build has ``IGNORE_USER_CONFIG`` (its ``app_server_flags``)."""
    if IGNORE_USER_CONFIG not in PIN.get("app_server_flags", ()):
        raise _refused(NO_FLAG, version=PIN["version"])


def _rest(args):
    return [str(arg) if isinstance(arg, os.PathLike) else arg for arg in args[1:]]


def allowed_command(args):
    """Whether ``args`` (an argv list whose first item is the agent runtime) is one Angelo starts it with."""
    rest = _rest(args)
    if tuple(rest) in FIXED_COMMANDS or tuple(rest) in wire_listings(PIN.get("wire_apis", ())):
        return True
    if rest[:2] == ["app-server", IGNORE_USER_CONFIG]:
        options = rest[2:]
        pairs = [(options[index], options[index + 1]) for index in range(0, len(options) - 1, 2)]
        # ``--listen`` only on stdio (re-read NIT-1: a ws:// address would serve the app-server on the network).
        return len(options) % 2 == 0 and all(name in APP_SERVER_OPTIONS and (name != "--listen" or value == LISTEN)
                                              for name, value in pairs)
    while len(rest) > 1 and rest[0] == "-c" and isinstance(rest[1], str) and rest[1].startswith(LOGIN_ROOT_OPTION):
        rest = rest[2:]
    return rest[:1] == ["login"] and tuple(rest[1:]) in LOGIN_TAILS


def check_command(args):
    """Refuse a start of the agent runtime that is not one ``allowed_command`` names (a string command line too:
    it can carry anything), and every app-server start on a pin without ``IGNORE_USER_CONFIG``."""
    if isinstance(args, (str, bytes)):
        raise _refused(REFUSED_START)
    if any(arg == "app-server" for arg in args[1:]):
        require_user_config_ignored()
    if not allowed_command(args):
        raise _refused(REFUSED_START)


def _spellings(environ=None):
    """The agent runtime's executable, as configured and resolved, and the bundled runtime's own folder (an override
    may sit in a shared folder such as /usr/local/bin); ``()`` without a runtime."""
    try:
        runtime = executable(environ)
    except RpcError:
        return ()
    if not runtime:
        return ()
    found = {runtime, os.path.realpath(runtime)}
    if override(environ) is None:   # The bundled runtime: its folder holds nothing else Angelo starts.
        found |= {os.path.dirname(runtime), os.path.dirname(os.path.realpath(runtime))}
    return tuple(sorted(spelling for spelling in found if spelling))


def names_runtime(command_line, environ=None):
    """Whether the command-line string ``command_line`` names the agent runtime's executable or its folder, as
    configured or resolved (``cd <folder> && ./codex``)."""
    text = os.fsdecode(command_line) if isinstance(command_line, bytes) else str(command_line)
    return any(spelling in text for spelling in _spellings(environ))


def _environment_path(env):
    return next((value for key, value in env.items() if key.upper() == "PATH"), os.defpath)


def _windows_search_path():
    """The folders CreateProcess searches for a bare program name: the parent's own executable's folder, its working
    folder, the system folders and the parent's PATH (never the child's environment)."""
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"
    folders = [os.path.dirname(sys.executable), os.getcwd(), os.path.join(root, "System32"), root,
               _environment_path(os.environ)]
    return os.pathsep.join(folder for folder in folders if folder)


def started_programs(args, kwargs, windows=None):
    """Every program a start of the argv list ``args`` with ``Popen``'s ``kwargs`` may run, as absolute paths: the
    check refuses when any of them is the runtime (re-read of 0b6b952594, SHOULD-1: decide on what runs, not what is
    written). ``executable=`` wins over ``args[0]``; bytes are decoded. A relative path joins ``cwd`` (POSIX) and the
    parent's working folder; a bare name is looked up on the child's PATH (POSIX). On Windows (CreateProcess) a path
    without an extension also gets ``.exe``, and a bare name is also searched in the parent's own folders."""
    windows = sys.platform == "win32" if windows is None else windows   # The host OS, whatever os.name reads.
    name = kwargs.get("executable") or (args[0] if args else None)
    try:
        name = os.fsdecode(name)
    except TypeError:
        return []
    if not name:
        return []
    env = kwargs.get("env")
    env = os.environ if env is None else env
    found = []
    if os.sep in name or (os.altsep and os.altsep in name) or os.path.isabs(name):
        cwd = kwargs.get("cwd")
        for folder in ([os.fsdecode(cwd)] if cwd is not None else []) + [os.getcwd()]:
            path = os.path.abspath(os.path.join(folder, name))
            found += [path] + ([path + ".exe"] if windows and not os.path.splitext(path)[1] else [])
    else:
        searches = [_environment_path(env)] + ([_windows_search_path()] if windows else [])
        for search in searches:
            for candidate in (name, name + ".exe") if windows and not os.path.splitext(name)[1] else (name,):
                hit = shutil.which(candidate, path=search)
                if hit:
                    found.append(os.path.abspath(hit))
    return list(dict.fromkeys(found))


def started_program(args, kwargs, windows=None):
    """The first of ``started_programs`` that exists, else the first (what an error would name), else None."""
    programs = started_programs(args, kwargs, windows)
    return next((path for path in programs if os.path.isfile(path)), programs[0] if programs else None)


def check_start(args, kwargs):
    """``RpcError`` for any start of the agent runtime that is not one Angelo builds, however it is spelled: a
    string or ``shell=True`` command that names it, a wrapper whose arguments name it (``env``, ``sh -c``), or an argv
    whose resolved program is it with a command line ``allowed_command`` refuses. Returns that program (None when the
    start is not of the runtime)."""
    if isinstance(args, (str, bytes)) or kwargs.get("shell"):
        text = args if isinstance(args, (str, bytes)) else " ".join(os.fsdecode(arg) for arg in args)
        if names_runtime(text):
            raise _refused(REFUSED_START)
        return None
    if not isinstance(args, (list, tuple)) or not args:
        return None
    program = next((path for path in started_programs(args, kwargs) if is_agent_runtime(path)), None)
    runtime = program is not None
    for argument in args[1:]:   # A wrapper: the runtime as an argument of another program.
        if isinstance(argument, (str, bytes, os.PathLike)) and names_runtime(os.fsdecode(argument)):
            raise _refused(REFUSED_START)
    if runtime:
        check_command([program, *args[1:]])
        if tuple(_rest(args)) in wire_listings(PIN.get("wire_apis", ())) and not listing_home(kwargs):
            raise _refused(REFUSED_START)
        return program
    return None


def supports_wire(wire):
    """Whether the pinned agent runtime speaks the model-provider ``wire_api`` ``wire``: Codex's own Responses wire,
    or one the build adds (``agent_runtime.json`` ``wire_apis``). The release checks ask the build to accept each
    listed wire (``scripts/angelo/release_checks.py``), so a mode that needs one is offered only with it
    (``provider_registry.available``)."""
    return wire == DEFAULT_WIRE or (wire in PIN.get("wire_apis", ()) and valid_wire(wire))


def platform_key(platform=None):
    """``macos``, ``windows``, ``linux`` or None (no other platform bundles a runtime)."""
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return "macos"
    if platform == "win32":
        return "windows"
    return "linux" if platform.startswith("linux") else None


def layout(platform=None):
    """This platform's pinned files: ``{"folder", "rust_target", "required", "optional"}`` (Windows also
    names ``upstream_signed`` and ``upstream_signer``: the files that keep OpenAI's own signature), or None."""
    key = platform_key(platform)
    return PIN["platforms"][key] if key else None


def _binary_path():
    """The running app's executable (``bpy.app.binary_path``), or None outside Blender."""
    bpy = sys.modules.get("bpy")
    path = getattr(getattr(bpy, "app", None), "binary_path", None)
    return path or None


def install_root(platform=None, binary=None, module=__file__):
    """The app bundle (macOS) or install folder (Windows, the extracted folder on Linux) this add-on runs from,
    or None.

    From the running executable when known (``Angelo.app/Contents/MacOS/Angelo``,
    ``<install>\\Angelo.exe``, ``<folder>/angelo``), else from this file's installed place
    (``Angelo.app/Contents/Resources/<version>/scripts/addons_core/angelo_agent``,
    ``<install>\\<version>\\scripts\\addons_core\\angelo_agent``, the same below the Linux folder),
    unresolved, so an add-on linked into an app for testing still finds that app.
    """
    key = platform_key(platform)
    if key is None:
        return None
    if binary:
        path = Path(binary)
        if key == "macos":
            return path.parents[2] if len(path.parents) > 2 and path.parent.name == "MacOS" else None
        return path.parent
    parents = Path(module).parents
    depth = 6 if key == "macos" else 4
    return parents[depth] if len(parents) > depth else None


@functools.lru_cache(maxsize=8)
def _bundled(platform, binary, module):
    files = layout(platform)
    root = install_root(platform, binary, module)
    if files is None or root is None:
        return None
    path = root / files["folder"] / files["required"][0]
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def bundled(platform=None, binary=None, module=__file__):
    """The bundled ``codex`` executable, or None (a development build without one). Looked up once per process."""
    return _bundled(sys.platform if platform is None else platform,
                    binary if binary is not None else _binary_path(), module)


@functools.lru_cache(maxsize=8)
def _release_build(platform, binary, module):
    files = layout(platform)
    root = install_root(platform, binary, module)
    return bool(files and root and (root / files["release_marker"]).is_file())


def release_build(platform=None, binary=None, module=__file__):
    """Whether this app is a release build: the pin's ``release_marker`` in its install, by itself. A release whose
    runtime is missing or not executable (quarantined, a partial update) is still one: ``executable`` refuses it
    rather than running the person's own Codex with their config (re-review of ffa4cb56717, MUST-2)."""
    return _release_build(sys.platform if platform is None else platform,
                          binary if binary is not None else _binary_path(), module)


def _is_executable(path):
    return Path(path).is_file() and os.access(path, os.X_OK)


def override(environ=None):
    """``ANGELO_AGENT_RUNTIME`` exactly as set (``""`` when empty), or None when unset. No file is read."""
    return product_env.current(OVERRIDE, environ)


def executable(environ=None):
    """The agent runtime's ``codex`` executable, or None when this app runs the person's own Codex.

    Raises ``RpcError`` when ``ANGELO_AGENT_RUNTIME`` is set but empty, names something that is not an
    executable file, or names any path in a release build.
    """
    value = override(environ)
    if value is None:
        found = bundled()
        if found is None and release_build():
            raise _refused(MISSING)
        return found
    if not value.strip():
        raise _refused(EMPTY_OVERRIDE, off=OFF)
    if value.strip().lower() == OFF:
        return None
    if release_build():
        raise _refused(RELEASE_OVERRIDE, value=value, off=OFF)
    path = os.path.expanduser(value.strip())
    if not os.path.isabs(path) or not _is_executable(path):
        raise _refused(NOT_EXECUTABLE, value=value, off=OFF)
    return path


def present(environ=None):
    """Whether Codex connections run the agent runtime (a misconfigured override counts as present: it is
    an error to show, not a reason to suggest installing Codex). Drawing code asks this: the answer for one
    ``ANGELO_AGENT_RUNTIME`` value is looked up once per process."""
    return _present(override(environ))


@functools.lru_cache(maxsize=4)
def _present(value):
    try:
        return executable({product_env.PREFIX + OVERRIDE: value} if value is not None else {}) is not None
    except RpcError:
        return True


def is_agent_runtime(path, environ=None):
    """Whether ``path`` is the agent runtime's executable (the same file, however it is spelled)."""
    try:
        runtime = executable(environ)
    except RpcError:
        return False
    if not runtime or not path:
        return False
    try:
        return os.path.samefile(runtime, path)
    except OSError:
        return False


def kind(path, environ=None):
    """Which Codex ``path`` is, for a request's receipt: ``override``, ``bundled`` or ``own``."""
    if is_agent_runtime(path, environ):
        return "override" if override(environ) is not None else "bundled"
    return "own"


def reported_version(user_agent):
    """The full version an ``initialize`` user agent reports (``angelo_blender/0.162.0-alpha.17.2 (...)``)."""
    match = _VERSION.search(user_agent) if isinstance(user_agent, str) else None
    return match.group(1) if match else None


KINDS = ("bundled", "override", "own")   # ``kind``'s answers.


def launched(executable_path, environ=None):
    """What Angelo tells a worker chat of the Codex it launched for it (design A', ``runtime_service``): ``{kind,
    version}`` as Angelo resolved them, ``version`` the pinned one an agent runtime must report (None for the
    person's own Codex). The worker checks the process against this (``verify``'s ``pinned``), never against a runtime
    it resolves itself; the agent runtime's files are checked here, where it runs."""
    which = kind(executable_path, environ)
    if which == "own":
        return {"kind": which, "version": None}
    check_files(executable_path)
    return {"kind": which, "version": PIN["version"]}


def verify(executable_path, initialized, pinned=None):
    """Refuse a process of the agent runtime that is not the pinned build (``initialize``'s result).

    The version must be the pinned one exactly (the build's pre-release suffix included), and the code-mode
    host must sit beside the executable, where Codex looks for it. ``pinned``: a worker chat's process, checked
    against the version Angelo resolved when it launched it (``launched``, which checked its files).
    """
    expected = PIN["version"] if pinned is None else pinned
    found = reported_version((initialized or {}).get("userAgent") if isinstance(initialized, dict) else None)
    if found != expected:
        from .display_text import Text
        raise _refused(WRONG_VERSION, path=str(executable_path), found=found or Text(UNREADABLE_VERSION),
                       expected=expected)
    if pinned is None:
        check_files(executable_path)


def check_files(executable_path):
    """The agent runtime's code-mode host sits beside ``executable_path`` (``RpcError`` otherwise)."""
    files = layout()
    if files is not None:
        host = Path(executable_path).parent / files["required"][1]
        if not host.is_file():
            raise _refused(INCOMPLETE, name=host.name, path=str(executable_path))
