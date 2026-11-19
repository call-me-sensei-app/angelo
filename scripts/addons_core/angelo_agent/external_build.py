# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which Angelo build is at the other end of access for external apps, and what to tell the person.

Every Angelo build a user runs shares one profile, so one endpoint (``external_cli``): the first build to turn on
access holds it, and an AI app's MCP entry may start another build's launcher. ``CLI_PROTOCOL`` covers the
framing and proofs, which did not change in 2.0.1; ``AGENT_PROTOCOL`` covers the commands a launcher sends, which
did (2.0.0's launcher calls tools, 2.0.1's talks to Angelo's agent). The host publishes it with its version in the
endpoint record and the welcome, the launcher sends it in its hello, and each side refuses a mismatch with what
to do instead of an unknown command. 2.0.0 publishes neither, so a build without them is named by the bundle its
process runs (``process_version``).

Standard library only; no bpy. Text here is for the external agent and the CLI (English, never translated); the
panel translates its own (``providers_page_external``).
"""

import os
import plistlib
import sys
from pathlib import Path

from . import app_version

# The launcher-to-host command set: 1 is the agent-only MCP (status, session new, agent). Raise it when a launcher
# would send a command an older host does not know, or stop sending one a newer host still expects.
AGENT_PROTOCOL = 1
OFF_THERE = "Preferences > AI Providers > External AI apps"


def identity():
    """What this build publishes about itself in the endpoint record and the welcome."""
    return {"angelo_version": app_version.current(), "agent_protocol": AGENT_PROTOCOL}


def executable(pid, platform=None):
    """The file the process ``pid`` runs (links resolved), or None. macOS: libproc's ``proc_pidpath`` (as
    ``update_stage_mac``); Linux: ``/proc/<pid>/exe``."""
    platform = platform or sys.platform
    try:
        if platform == "darwin":
            import ctypes
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            buffer = ctypes.create_string_buffer(4096)   # PROC_PIDPATHINFO_MAXSIZE
            return os.fsdecode(buffer.value) if libproc.proc_pidpath(int(pid), buffer, 4096) > 0 else None
        if platform.startswith("linux"):
            return os.readlink(f"/proc/{int(pid)}/exe")
    except (OSError, ValueError, AttributeError):
        return None
    return None


def bundle_version(path):
    """The Angelo version of the macOS bundle ``path`` lies in (its Info.plist ``CFBundleShortVersionString``, which
    the build sets to ``ANGELO_APP_VERSION``), or None."""
    for parent in Path(path).parents if path else ():
        if parent.suffix == ".app":
            try:
                with open(parent / "Contents" / "Info.plist", "rb") as handle:
                    version = plistlib.load(handle).get("CFBundleShortVersionString")
            except (OSError, ValueError, plistlib.InvalidFileException):
                return None
            return version if app_version.parse(version) else None
    return None


def process_version(pid):
    return bundle_version(executable(pid))


def holder_version(record):
    """The version of the build that published ``record``: its own word, else the bundle its process runs."""
    version = record.get("angelo_version") if isinstance(record, dict) else None
    if app_version.parse(version):
        return version
    pid = record.get("pid") if isinstance(record, dict) else None
    return process_version(pid) if type(pid) is int and pid > 0 else None


def record_mismatch(record):
    """What a launcher says before any call when the endpoint ``record`` was published by a live build of another
    command set (2.0.0 publishes none), or "": a stale record of a dead process is the connection's to report."""
    if not isinstance(record, dict) or record.get("agent_protocol") == AGENT_PROTOCOL:
        return ""
    pid = record.get("pid")
    from .engine_link_protocol import process_alive   # Platform-aware (never a signal-0 kill: Windows' Ctrl+C).
    if type(pid) is not int or pid <= 0 or not process_alive(pid):
        return ""
    theirs = record.get("agent_protocol")
    return host_mismatch(holder_version(record), pid, theirs if type(theirs) is int else None)


def name(version):
    return f"Angelo {version}" if version else "another Angelo"


def _capitalized(text):
    return text[:1].upper() + text[1:]


def host_mismatch(version, pid, theirs):
    """A launcher's answer when the host holding access speaks another command set (``theirs``: None for 2.0.0)."""
    mine = app_version.current()
    holder = _capitalized(name(version)) + (f" (PID {pid})" if pid else "")
    if theirs is not None and theirs > AGENT_PROTOCOL:
        return (f"{holder} has access for external apps on this computer and is newer than this MCP server "
                f"(Angelo {mine}). Ask the person to press Connect for this app in {name(version)} ({OFF_THERE}), "
                "then start a new session in this app.")
    return (f"{holder} has access for external apps on this computer, and it cannot run this MCP server's commands "
            f"(Angelo {mine}). Ask the person to quit it, or turn off access there ({OFF_THERE}), then turn it on "
            f"in Angelo {mine}.")


def launcher_mismatch(theirs):
    """The host's answer to a launcher that speaks another command set (``theirs``: None for 2.0.0's launcher)."""
    mine = app_version.current()
    which = "a newer" if theirs is not None and theirs > AGENT_PROTOCOL else "an older"
    return (f"This app's MCP entry starts {which} Angelo's MCP server, which cannot work with Angelo {mine}. Ask the "
            f"person to press Connect for this app in Angelo {mine} ({OFF_THERE}), then start a new session in "
            "this app.")


def holder_note(version, pid):
    """The host's own status line when another build holds the endpoint (English; the panel shows it translated)."""
    return (f"{_capitalized(name(version))} (PID {pid}) has access for external apps for your user. Quit it, or turn "
            "off access there; this Angelo takes over once it is free.")
