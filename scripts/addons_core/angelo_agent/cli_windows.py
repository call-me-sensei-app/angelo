# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the official CLIs live on Windows, and how to start an npm shim without cmd.exe.

``cli_runtime.find_executable`` on Windows tries, in order: the configured file,
PATH, the ChatGPT app's bundled ``codex``, the well-known directories below
(nothing is spawned) and the PATH Windows saved for this user and machine (the
registry, which holds directories installed after Angelo started).

npm, pnpm and Yarn install a CLI as a ``.cmd`` batch shim. CreateProcess runs a
batch file through cmd.exe, which parses every argument again (quotes, ``%``,
``^``, ``&``: the "BatBadBut" class of bugs), and Angelo's arguments carry JSON.
So a shim is never run: ``launch_command`` reads the script it names and starts
``node.exe <script>`` (or the ``.exe`` it names) directly. A batch file that is
not a recognised shim runs through cmd.exe only when every argument is a plain
token; anything else is refused with what to do instead.

Claude Code on Windows runs its commands through Git Bash (Git for Windows);
``git_bash`` finds it as Claude Code does (CLAUDE_CODE_GIT_BASH_PATH, then the
``git`` on PATH) and in Git's standard install folders.
"""

import ntpath
import os
import re
from pathlib import Path

VERSION_DIRECTORIES = 6
SHIM_BYTES = 65536
_TARGET = re.compile(r'"%~?dp0%?\\([^"%\r\n]+)"\s+%\*')
_NODE_PATH = re.compile(r'SET\s+"NODE_PATH=([^"\r\n]*)"', re.IGNORECASE)
_PLAIN = re.compile(r"[A-Za-z0-9_\-+=.:/\\@]+")
_SCRIPTS = {".js", ".cjs", ".mjs"}
GIT_BASH_MISSING = ("Claude Code on Windows runs its commands through Git Bash, which comes with Git for Windows. "
                    "Install Git for Windows (https://git-scm.com/downloads/win, or run: winget install Git.Git), "
                    "then reconnect. If Git is installed in another folder, set CLAUDE_CODE_GIT_BASH_PATH to its "
                    "bash.exe and restart Angelo.")
INSTALL = {"claude": "npm install -g @anthropic-ai/claude-code", "codex": "npm install -g @openai/codex"}


def _variable(environ, name):
    """Windows variables are case-insensitive; host tests pass plain dicts."""
    for key, value in environ.items():
        if key.upper() == name.upper() and value:
            return value
    return ""


def _version_key(name):
    return tuple(int(part) for part in re.findall(r"\d+", name)[:3])


def _newest(root, relative=""):
    try:
        names = sorted((entry.name for entry in os.scandir(root) if entry.is_dir()), key=_version_key, reverse=True)
    except OSError:
        return []
    return [Path(root, name, relative) if relative else Path(root, name) for name in names[:VERSION_DIRECTORIES]]


def known_directories(environ, home):
    """Existing directories where npm, pnpm, Volta, Scoop, WinGet, Chocolatey or a native installer put CLIs."""
    get = lambda name: _variable(environ, name)
    candidates = []
    for name, relative in (("NPM_CONFIG_PREFIX", ""), ("VOLTA_HOME", "bin"), ("PNPM_HOME", ""), ("BUN_INSTALL", "bin"),
                           ("NVM_SYMLINK", ""), ("SCOOP", "shims"), ("ChocolateyInstall", "bin")):
        if get(name) and os.path.isabs(get(name)):
            candidates.append(Path(get(name), relative) if relative else Path(get(name)))
    appdata, local = get("APPDATA"), get("LOCALAPPDATA")
    if appdata:
        candidates.append(Path(appdata, "npm"))
    if local:
        candidates += [Path(local, "Microsoft", "WindowsApps"), Path(local, "Microsoft", "WinGet", "Links"),
                       Path(local, "Volta", "bin"), Path(local, "pnpm")]
    candidates += [Path(home, ".local", "bin"), Path(home, ".bun", "bin"), Path(home, "scoop", "shims"),
                   Path(home, ".cargo", "bin")]
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if get(name):
            candidates.append(Path(get(name), "nodejs"))
    if get("ProgramData"):
        candidates.append(Path(get("ProgramData"), "chocolatey", "bin"))
    if appdata:
        candidates += _newest(Path(appdata, "fnm", "node-versions"), "installation")
    if get("NVM_HOME"):
        candidates += _newest(Path(get("NVM_HOME")))
    unique = []
    for directory in candidates:
        if str(directory) not in unique and directory.is_dir():
            unique.append(str(directory))
    return unique


def chatgpt_codex(environ):
    """The ChatGPT app's bundled codex, where its Windows installer puts it (a bounded list of files)."""
    get = lambda name: _variable(environ, name)
    roots = [Path(get("LOCALAPPDATA"), "Programs", "ChatGPT")] if get("LOCALAPPDATA") else []
    roots += [Path(get(name), "ChatGPT") for name in ("ProgramFiles", "ProgramW6432") if get(name)]
    if get("LOCALAPPDATA"):
        roots += _newest(Path(get("LOCALAPPDATA"), "ChatGPT"))   # Versioned app-x.y.z folders.
    found = []
    for root in roots:
        for relative in (("resources", "codex-cli", "codex.exe"), ("resources", "codex-cli", "bin", "codex.exe")):
            if Path(root, *relative).is_file():
                found.append(str(Path(root, *relative)))
    return found


def registry_path(read=None):
    """The user's and the machine's saved PATH directories (new installs reach them before Angelo restarts)."""
    if read is None:
        read = _read_registry_path
    directories = []
    for value in read():
        for directory in value.split(";"):
            directory = directory.strip().strip('"')
            if os.path.isabs(directory) and directory not in directories and os.path.isdir(directory):
                directories.append(directory)
    return directories[:64]


def _read_registry_path():
    import winreg
    values = []
    for root, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(root, key) as handle:
                value, kind = winreg.QueryValueEx(handle, "Path")
        except OSError:
            continue
        if isinstance(value, str):
            values.append(winreg.ExpandEnvironmentStrings(value) if kind == winreg.REG_EXPAND_SZ else value)
    return values


def resolve_shim(path, find_node, depth=0):
    """``([program, ...], extra environment)`` for an npm/pnpm/Yarn ``.cmd`` shim, else None.

    npm also writes an extensionless shell script and a ``.ps1`` beside the
    ``.cmd``; choosing either resolves through the ``.cmd``.
    """
    path = Path(path)
    if path.suffix.lower() in {"", ".ps1"} and path.with_suffix(".cmd").is_file():
        path = path.with_suffix(".cmd")
    if path.suffix.lower() not in {".cmd", ".bat"} or depth > 2:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(SHIM_BYTES)
    except OSError:
        return None
    targets = _TARGET.findall(text)
    if not targets:
        return None
    target = Path(os.path.normpath(path.parent.joinpath(*targets[-1].split("\\"))))
    if not target.is_file():
        return None
    environment = {}
    node_path = _NODE_PATH.search(text)
    if node_path and os.path.isabs(node_path.group(1).split(";")[0]):
        environment["NODE_PATH"] = node_path.group(1)
    suffix = target.suffix.lower()
    if suffix in _SCRIPTS:
        node = path.parent / "node.exe"
        return [str(node) if node.is_file() else find_node(), str(target)], environment
    if suffix == ".exe":
        return [str(target)], environment
    if suffix in {".cmd", ".bat"}:
        return resolve_shim(target, find_node, depth + 1)
    return None


class UnsafeBatchArguments(ValueError):
    pass


def launch_command(command, find_node, environ):
    """``(args, extra environment)`` that start ``command`` without cmd.exe re-parsing its arguments.

    ``args`` is a list, or for a non-shim batch file with only plain tokens, the
    exact ``cmd.exe /d /s /c`` command line as a string.
    """
    executable = command[0]
    resolved = resolve_shim(executable, find_node)
    if resolved is not None:
        program, environment = resolved
        return program + list(command[1:]), environment
    if Path(executable).suffix.lower() not in {".cmd", ".bat"}:
        return list(command), {}
    if '"' in executable or "%" in executable or any(not _PLAIN.fullmatch(argument) for argument in command[1:]):
        raise UnsafeBatchArguments(
            "Angelo can't pass its arguments safely through the batch file " + executable + ". Choose the CLI's "
            ".exe (or, for an npm install, run `where.exe` on it and choose the .cmd npm created) as the connection's "
            "Executable, then reconnect.")
    shell = _variable(environ, "ComSpec") or ntpath.join(_variable(environ, "SystemRoot") or "C:\\Windows", "System32", "cmd.exe")
    return '"%s" /d /s /c ""%s" %s"' % (shell, executable, " ".join(command[1:])), {}


def git_bash(environ, which):
    """Git Bash for Claude Code: CLAUDE_CODE_GIT_BASH_PATH, the ``git`` on PATH, then Git's standard folders."""
    configured = _variable(environ, "CLAUDE_CODE_GIT_BASH_PATH")
    if configured:
        return configured if Path(configured).is_file() else None
    candidates = []
    git = which("git")
    if git:
        folder = Path(git).parent   # <Git>\cmd\git.exe or <Git>\bin\git.exe
        candidates += [folder.parent / "bin" / "bash.exe", folder / "bash.exe"]
    for name, relative in (("ProgramFiles", ("Git",)), ("ProgramW6432", ("Git",)), ("ProgramFiles(x86)", ("Git",)),
                           ("LOCALAPPDATA", ("Programs", "Git"))):
        if _variable(environ, name):
            candidates.append(Path(_variable(environ, name), *relative, "bin", "bash.exe"))
    if _variable(environ, "USERPROFILE"):
        candidates.append(Path(_variable(environ, "USERPROFILE"), "scoop", "apps", "git", "current", "bin", "bash.exe"))
    return next((str(path) for path in candidates if path.is_file()), None)
