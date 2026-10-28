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
   under a shipped app's sign-in.
2. The bundled runtime: ``Angelo.app/Contents/Helpers/codex`` on macOS,
   ``<install folder>\\agent-runtime\\codex.exe`` on Windows, beside its
   ``codex-code-mode-host`` (Codex finds that next to its own executable).
3. Otherwise none: the person's own Codex, found as before (development builds).

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
import sys
from pathlib import Path

from . import product_env
from .protocol import RpcError

OVERRIDE = "AGENT_RUNTIME"  # ANGELO_AGENT_RUNTIME (product_env)
OFF = "off"
PIN = json.loads((Path(__file__).resolve().parent / "agent_runtime.json").read_text(encoding="utf-8"))
_VERSION = re.compile(r"/(\d+\.\d+\.\d+[^\s(;]*)")


def platform_key(platform=None):
    """``macos``, ``windows`` or None (no platform bundles a runtime)."""
    platform = sys.platform if platform is None else platform
    return "macos" if platform == "darwin" else "windows" if platform == "win32" else None


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
    """The app bundle (macOS) or install folder (Windows) this add-on runs from, or None.

    From the running executable when known (``Angelo.app/Contents/MacOS/Angelo``,
    ``<install>\\Angelo.exe``), else from this file's installed place
    (``Angelo.app/Contents/Resources/<version>/scripts/addons_core/angelo_agent``,
    ``<install>\\<version>\\scripts\\addons_core\\angelo_agent``), unresolved, so an
    add-on linked into an app for testing still finds that app.
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
    return bool(files and root and (root / files["release_marker"]).is_file()) and bool(
        _bundled(platform, binary, module))


def release_build(platform=None, binary=None, module=__file__):
    """Whether this app is a release build: the bundled runtime and the pin's ``release_marker`` beside it."""
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
        return bundled()
    if not value.strip():
        raise RpcError(f"ANGELO_AGENT_RUNTIME is set but empty. Point it at the agent runtime's codex executable, "
                       f"set it to {OFF} to use your own Codex, or unset it.")
    if value.strip().lower() == OFF:
        return None
    if release_build():
        raise RpcError(f"This release of Angelo runs only its own agent runtime, so ANGELO_AGENT_RUNTIME cannot name "
                       f"another Codex ({value}). Unset it, or set it to {OFF} to use your own Codex.")
    path = os.path.expanduser(value.strip())
    if not os.path.isabs(path) or not _is_executable(path):
        raise RpcError(f"ANGELO_AGENT_RUNTIME names {value}, which is not an executable file. Point it at the agent "
                       f"runtime's codex executable, set it to {OFF} to use your own Codex, or unset it.")
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
        raise RpcError(f"Angelo's agent runtime ({executable_path}) reports Codex {found or 'an unreadable version'}, "
                       f"but this version of Angelo runs {expected}. Reinstall Angelo, or point "
                       "ANGELO_AGENT_RUNTIME at the pinned build.")
    if pinned is None:
        check_files(executable_path)


def check_files(executable_path):
    """The agent runtime's code-mode host sits beside ``executable_path`` (``RpcError`` otherwise)."""
    files = layout()
    if files is not None:
        host = Path(executable_path).parent / files["required"][1]
        if not host.is_file():
            raise RpcError(f"Angelo's agent runtime is incomplete: {host.name} is missing beside {executable_path}. "
                           "Reinstall Angelo.")
