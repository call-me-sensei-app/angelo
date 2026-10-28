# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the official CLIs are, and which versions Angelo has been tested with.

A Finder-launched macOS app inherits the minimal ``/usr/bin:/bin:/usr/sbin:/sbin``
PATH, so ``shutil.which`` alone misses Homebrew, npm-global and nvm/Volta/asdf
installs. Codex is Angelo's agent runtime when the app has one (``agent_runtime``);
otherwise discovery tries, in order: the configured path, PATH, the
fixed app-bundle path, a bounded list of well-known directories (no process is
spawned) and, only when all of those miss, the PATH a login shell exports (one
probe per process, with a timeout; doc/angelo/ai_providers.rst). Windows has its
own locations, npm's ``.cmd`` shims and Claude Code's Git Bash (``cli_windows``);
``launch`` and ``run_cli`` start a CLI there without cmd.exe, a console window
or an unkillable process tree (``child_process``).

The tested ranges (``RUNTIMES``) are the versions whose protocol Angelo relies
on: Codex ``experimentalApi`` and undocumented ``-c`` keys, and Claude Code
``initialize.jsonSchema``. Below the floor a runtime is refused. Above the
ceiling it runs exactly as one inside the range: Angelo follows the latest
releases, which update themselves, so a newer one is never refused, warned
about in the chat or asked to downgrade; it is only recorded in one quiet log
line per process and version (``note_newer``).
"""

import os
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import agent_runtime, child_process, cli_windows
from .protocol import RpcError

RUNTIMES = {
    # Moved at every release (doc/angelo/ai_providers.rst, "Claude Code's range"): the floor is the newest release
    # that passed runtime_launch_check.py and that Claude Code's stable channel (the default Homebrew cask) delivers;
    # the ceiling the newest the release's gates ran on (2.1.301).
    "claude": {"label": "Claude Code", "floor": (2, 1, 292), "ceiling": (2, 1, 301),
               "relies_on": "stream-json initialize.jsonSchema and systemPrompt",
               "update": "claude update, npm install -g @anthropic-ai/claude-code or brew upgrade --cask claude-code"},
    # 0.162.0-alpha.2 (the ChatGPT app's bundled Codex from 2026-10-09) passed the release's zero-failure
    # gates; a pre-release counts as its release (0, 162, 0).
    "codex": {"label": "Codex", "floor": (0, 160, 0), "ceiling": (0, 162, 0),
              "relies_on": "app-server experimentalApi and the undocumented -c keys",
              "update": "npm install -g @openai/codex, or brew upgrade --cask codex"},
}
INSTALL = {"claude": "npm install -g @anthropic-ai/claude-code, or brew install --cask claude-code",
           "codex": "npm install -g @openai/codex, or brew install --cask codex"}
CODEX_BUNDLE = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"

PROBE_SECONDS = 2.0          # The login-shell probe is killed after this long, once per process.
PROBE_OUTPUT_LIMIT = 16384   # Bytes of its output read; a profile that prints more is ignored.
VERSION_DIRECTORIES = 6      # Newest nvm/fnm node versions searched; never an unbounded walk.
# Under ~: version managers and package-manager prefixes, then the CLIs' own install locations.
HOME_DIRECTORIES = (".local/bin", ".npm-global/bin", ".volta/bin", ".asdf/shims", ".local/share/mise/shims",
                    ".nodenv/shims", ".bun/bin", ".yarn/bin", "Library/pnpm", ".local/share/pnpm", ".cargo/bin")
SYSTEM_DIRECTORIES = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin", "/usr/bin")
VERSION_HOMES = (".nvm/versions/node", ".local/share/fnm/node-versions", "Library/Application Support/fnm/node-versions")

_login_path = None
_login_lock = threading.Lock()
_noted = set()
_noted_lock = threading.Lock()


def _is_executable(path):
    return bool(path) and Path(path).is_file() and os.access(path, os.X_OK)


def _version_key(name):
    return tuple(int(part) for part in re.findall(r"\d+", name)[:3])


def _version_directories(home):
    """``bin`` of the newest few node versions of nvm and fnm, if present."""
    found = []
    for relative in VERSION_HOMES:
        root = home / relative
        try:
            names = sorted((entry.name for entry in os.scandir(root) if entry.is_dir()), key=_version_key, reverse=True)
        except OSError:
            continue
        found += [root / name / ("installation/bin" if "fnm" in relative else "bin") for name in names[:VERSION_DIRECTORIES]]
    return found


def known_directories(environ=None, home=None):
    """Existing, de-duplicated directories where these CLIs are normally installed (no process spawned)."""
    environ, home = os.environ if environ is None else environ, Path.home() if home is None else Path(home)
    configured = []
    for variable, relative in (("NPM_CONFIG_PREFIX", "bin"), ("npm_config_prefix", "bin"), ("VOLTA_HOME", "bin"),
                               ("ASDF_DATA_DIR", "shims"), ("PNPM_HOME", ""), ("BUN_INSTALL", "bin"), ("N_PREFIX", "bin")):
        if environ.get(variable) and os.path.isabs(environ[variable]):
            configured.append(Path(environ[variable]) / relative)
    candidates = [*configured, *(home / relative for relative in HOME_DIRECTORIES), *map(Path, SYSTEM_DIRECTORIES),
                  *_version_directories(home)]
    unique = []
    for directory in candidates:
        if str(directory) not in unique and directory.is_dir():
            unique.append(str(directory))
    return unique


def _run_login_shell(shell, timeout):
    """A login shell's framed ``$PATH`` output, or None. At most PROBE_OUTPUT_LIMIT bytes and ``timeout`` seconds."""
    frame = "__ANGELO_PATH__"
    body = "string join : $PATH" if Path(shell).name == "fish" else 'printf %s "$PATH"'
    environment = {"HOME": str(Path.home()), "SHELL": shell, "TERM": "dumb", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                   "USER": os.environ.get("USER", ""), "LOGNAME": os.environ.get("LOGNAME", "")}
    try:
        process = subprocess.Popen([shell, "-l", "-c", f"printf %s {frame}; {body}; printf %s {frame}"], env=environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError:
        return None
    deadline, chunks, size, finished = time.monotonic() + timeout, [], 0, False
    try:
        descriptor = process.stdout.fileno()
        while size < PROBE_OUTPUT_LIMIT:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
                break
            chunk = os.read(descriptor, 4096)
            if not chunk:
                finished = True
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)  # The shell and anything its profile left running.
        except OSError:
            process.kill()
        process.stdout.close()
        process.wait()
    return b"".join(chunks).decode("utf-8", "replace") if finished else None


def login_shell_path(timeout=PROBE_SECONDS):
    """The PATH a login shell exports: probed once per process, only after every fixed location missed.

    ``-l`` reads the login profile (where Homebrew, Volta and asdf add their directories) but not an interactive
    rc file. The shell gets a minimal environment, no terminal and a sentinel-framed answer; it is killed (with its
    process group) after ``timeout`` seconds or ``PROBE_OUTPUT_LIMIT`` bytes. A failure is remembered as "no extra
    directories". The answer is only ever split into absolute, existing directories, never run.
    """
    global _login_path
    with _login_lock:
        if _login_path is not None:
            return _login_path
        _login_path = ""
        shell = os.environ.get("SHELL") or ("/bin/zsh" if sys.platform == "darwin" else "/bin/sh")
        if os.name == "nt" or not os.path.isabs(shell) or not os.access(shell, os.X_OK):
            return _login_path
        parts = (_run_login_shell(shell, timeout) or "").split("__ANGELO_PATH__")
        if len(parts) == 3:
            directories = []
            for directory in parts[1].split(os.pathsep)[:64]:
                if os.path.isabs(directory) and directory not in directories and os.path.isdir(directory):
                    directories.append(directory)
            _login_path = os.pathsep.join(directories)
        return _login_path


def _tilde(directory):
    home = str(Path.home())
    return "~" + directory[len(home):] if directory.startswith(home + os.sep) else directory


def not_found_message(name, configured="", searched=(), platform=None):
    """What to do when a CLI cannot be found: install it, or point Angelo at it."""
    spec, windows = RUNTIMES[name], (platform or sys.platform) == "win32"
    shown = ", ".join(_tilde(directory) for directory in searched[:8]) + (", ..." if len(searched) > 8 else "")
    lookup = f"`where.exe {name}` in PowerShell" if windows else f"`which {name}` in Terminal"
    return (f"{spec['label']} was not found" + (f" at the configured path {configured}" if configured else "")
            + f". Install the official CLI ({cli_windows.INSTALL[name] if windows else INSTALL[name]}), then reconnect. "
            f"If it is already installed, run {lookup} and choose that file as the connection's Executable. "
            + (f"Searched PATH and {shown}." if shown else "Searched PATH."))


def find_executable(name, configured=""):
    """The CLI to run for ``name``. For Codex, Angelo's agent runtime when there is one (``agent_runtime``)."""
    if name == "codex":
        runtime = agent_runtime.executable()
        if runtime:
            return runtime
    if os.name == "nt":
        return _find_windows(name, configured, os.environ)
    configured = (configured or "").strip()
    expanded = os.path.expanduser(configured)
    fixed = CODEX_BUNDLE if name == "codex" else ""
    for candidate in (expanded, shutil.which(name), fixed):
        if _is_executable(candidate):
            return candidate
    directories = known_directories()
    found = shutil.which(name, path=os.pathsep.join(directories))
    if found:
        return found
    shell_path = login_shell_path()
    found = shutil.which(name, path=shell_path) if shell_path else None
    if found:
        return found
    raise RpcError(not_found_message(name, configured, directories))


def _find_windows(name, configured, environ, registry=None):
    """Configured file, PATH, the ChatGPT app's codex, well-known folders, then the PATH saved in the registry."""
    configured = (configured or "").strip()
    found = None
    for candidate in (os.path.expandvars(os.path.expanduser(configured)) if configured else "", shutil.which(name),
                      *(cli_windows.chatgpt_codex(environ) if name == "codex" else ())):
        if candidate and Path(candidate).is_file():
            found = candidate
            break
    directories = cli_windows.known_directories(environ, Path.home())
    found = found or shutil.which(name, path=os.pathsep.join(directories))
    found = found or shutil.which(name, path=os.pathsep.join(cli_windows.registry_path(registry)))
    if not found:
        raise RpcError(not_found_message(name, configured, directories, platform="win32"))
    if name == "claude" and cli_windows.git_bash(environ, shutil.which) is None:
        raise RpcError(cli_windows.GIT_BASH_MISSING)
    return found


def _path_key(env, windows):
    return next((key for key in env if key.upper() == "PATH"), "PATH") if windows else "PATH"


def with_runtime_path(env, executable, windows=None):
    """``env`` with the directories an npm-installed CLI's interpreter lives in, after the person's PATH.

    An npm package's ``#!/usr/bin/env node`` fails under a Finder launch's minimal PATH even when the CLI itself
    was found. Unchanged when the executable's directory is already on PATH (found there, so is its interpreter).
    On Windows it also names Git Bash for Claude Code when only Angelo's search found it (other CLIs ignore it).
    """
    windows = os.name == "nt" if windows is None else windows
    key = _path_key(env, windows)
    path = env.get(key, "")
    own = os.path.dirname(executable)
    if windows and "CLAUDE_CODE_GIT_BASH_PATH" not in env:
        bash = cli_windows.git_bash(env, lambda name: shutil.which(name, path=path))
        if bash:
            env["CLAUDE_CODE_GIT_BASH_PATH"] = bash
    present = path.split(os.pathsep)
    same = (lambda first, second: first.lower() == second.lower()) if windows else str.__eq__
    if not own or any(same(own, directory) for directory in present):
        return env
    extra = ([own, *cli_windows.known_directories(os.environ, Path.home())] if windows
             else [own, *known_directories(), *(_login_path or "").split(os.pathsep)])
    env[key] = os.pathsep.join(present + [directory for directory in dict.fromkeys(extra)
                                          if directory and not any(same(directory, other) for other in present)])
    return env


def _find_node(env):
    """node.exe for an npm shim's script: on the child's PATH, in the well-known folders or the saved PATH."""
    path = os.pathsep.join([env.get(_path_key(env, True), ""), *cli_windows.known_directories(os.environ, Path.home()),
                            *cli_windows.registry_path()])
    found = shutil.which("node.exe", path=path)
    if not found:
        raise RpcError("Node.js was not found, and this CLI was installed with npm, which runs it with Node.js. Install "
                       "Node.js LTS (https://nodejs.org, or run: winget install OpenJS.NodeJS.LTS), then reconnect.")
    return found


def runtime_command(command, env, windows=None):
    """``command`` as Windows starts it without cmd.exe (an npm shim becomes node.exe and its script); else unchanged."""
    if not (os.name == "nt" if windows is None else windows):
        return command
    try:
        args, extra = cli_windows.launch_command(command, lambda: _find_node(env), env)
    except cli_windows.UnsafeBatchArguments as error:
        raise RpcError(str(error)) from None
    env.update(extra)
    return args


# In a worker Blender that hosts a chat, its model runtimes run in Angelo instead (``runtime_relay.install``): every
# provider launch goes through ``launch``, so this is the one place that chooses. None in Angelo itself.
relay = None


def launch(command, *, env, **kwargs):
    """Start a provider CLI: UTF-8 text, no console window, and terminate()/kill() end its whole process tree.

    ``keep_gui_programs=True`` (sign-in): ending it never closes a browser it opened (``child_process``).
    In a worker with ``relay`` installed, Angelo starts it and this returns its ``runtime_relay.RelayedProcess``.
    """
    if relay is not None:
        return relay(command, env=env, **kwargs)
    return child_process.popen(runtime_command(command, env), env=env, **kwargs)


def run_cli(command, *, env, **kwargs):
    """Run a provider CLI to completion like ``subprocess.run`` (a timeout ends its whole process tree)."""
    return child_process.run(runtime_command(command, env), env=env, **kwargs)


def format_version(version):
    return ".".join(map(str, version))


def too_old_message(name, found=None, platform=None):
    spec = RUNTIMES[name]
    update = cli_windows.INSTALL[name] if (platform or sys.platform) == "win32" else spec["update"]
    return (f"Angelo requires {spec['label']} {format_version(spec['floor'])} or newer ("
            + (f"found {format_version(found)}" if found else "its version could not be read")
            + f"). Update the official CLI ({update}), or choose a supported executable in the connection's settings.")


def newer_than_gated(name, version):
    """The log line for a runtime newer than the gated ceiling, else None (English: a log line, never shown)."""
    spec = RUNTIMES[name]
    if version <= spec["ceiling"]:
        return None
    return "%s %s is newer than the version Angelo's gates ran on (%s); running it as usual" % (  # i18n: log line
        spec["label"], format_version(version), format_version(spec["ceiling"]))


def note_newer(name, version):
    """Record a runtime above the gated ceiling: one quiet log line per process and version, nothing else.

    It runs exactly as a runtime inside the range does: nothing is refused,
    shown in the chat or suggested, since Angelo follows the latest releases
    (doc/angelo/ai_providers.rst, "Claude Code's range"). True when it logged.
    """
    line = newer_than_gated(name, version)
    if line is None:
        return False
    with _noted_lock:
        if (name, version) in _noted:
            return False
        _noted.add((name, version))
    try:
        sys.stderr.write("Angelo: %s\n" % line)
        sys.stderr.flush()
    except (AttributeError, OSError, ValueError):
        pass  # No usable console: the runtime runs all the same.
    return True


def codex_version(initialized):
    """The Codex version from an ``initialize`` result's ``userAgent``; below the floor raises.

    The agent is ``<client name>/<version> (<os>; <arch>) …`` (codex-cli 0.162:
    ``angelo_blender/0.162.0-alpha.2 (Mac OS 26.3.0; arm64) unknown (…)``). The
    first ``/X.Y.Z`` is the version, whatever the client name holds (spaces
    included); a pre-release suffix counts as its release.
    """
    agent = initialized.get("userAgent") if isinstance(initialized, dict) else None
    match = re.search(r"/(\d+)\.(\d+)\.(\d+)(?=[-+\s(]|$)", agent) if isinstance(agent, str) else None
    version = tuple(map(int, match.groups())) if match else None
    if version is None or version < RUNTIMES["codex"]["floor"]:
        raise RpcError(too_old_message("codex", version))
    return version


def note_codex_runtime(context, initialized, agent=None, pinned=None):
    """Refuse a Codex below the floor before any experimental request; log one above the ceiling (``note_newer``).

    ``agent``: the executable of Angelo's agent runtime when this process runs it; that build is pinned,
    so it must report exactly the pinned version (``agent_runtime.verify``) and is never newer. ``pinned``: the
    version Angelo resolved when it launched this worker chat's process (``agent_runtime.launched``).
    """
    version = codex_version(initialized)
    if agent:
        agent_runtime.verify(agent, initialized, pinned)
    else:
        note_newer("codex", version)
    return initialized
