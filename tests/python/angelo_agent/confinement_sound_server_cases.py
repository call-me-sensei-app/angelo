# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No program started through a sound server (test_blender_script_confinement's sound-server checks).

Opening an audaspace output device can start a program: libjack, unless told JackNoStartServer, runs the first line
of ``~/.jackdrc``; PulseAudio autospawns the ``daemon-binary`` its ``client.conf`` names. Both files are the person's,
and a script may write them. So a confined script's ``aud.Device`` is refused (PROGRAMS), and audaspace never asks
either server to start, for any caller: with ``~/.jackdrc`` and ``client.conf`` pointing at a program that writes a
marker, opening each device from Angelo's own code (no region) must leave no marker. Linux builds with JACK and
PulseAudio only; elsewhere the checks record that they were not run. Run only inside Blender, from the suite.
"""

import os
import stat
import sys
import tempfile
import time
from pathlib import Path

DEVICES = (("jack", "JACK"), ("pulse", "PulseAudio"))


def _program(folder, marker):
    """A program that writes ``marker`` when run (whatever arguments it gets)."""
    script = Path(folder) / "start-server.sh"
    script.write_text("#!/bin/sh\necho started \"$@\" >> '%s'\n" % marker)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def run_sound_server_cases(run, check, PROGRAMS, STARTUP=None):
    import aud
    home = Path(os.path.expanduser("~"))
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    # A script never writes the audio stack's configuration (os_startup; break-it-confine-fixes-b31e34840ca MUST-3):
    # an ALSA ~/.asoundrc can load a library or pipe to a command, PulseAudio's client.conf connects out, and so on.
    if STARTUP is not None and sys.platform != "win32":
        for label, path in (("asoundrc", home / ".asoundrc"), ("jackdrc", home / ".jackdrc"),
                            ("alsoftrc", home / ".alsoftrc"), ("pulse", config / "pulse" / "client.conf"),
                            ("pipewire", config / "pipewire" / "client.conf"), ("alsoft", config / "alsoft.conf"),
                            ("alsa_xdg", config / "alsa" / "asoundrc"),
                            ("pulse_legacy", home / ".pulse" / "client.conf")):
            # A red case may write the file or make its folder: put back exactly what was there.
            kept = path.read_bytes() if path.exists() else None
            missing = [folder for folder in reversed(path.parents) if not folder.exists()]
            outcome = run("import os\nos.makedirs(os.path.dirname(PATH), exist_ok=True)\n"
                          "with open(PATH, 'a') as handle:\n    handle.write('# angelo probe\\n')\n", PATH=str(path))
            check("audio_config_write_refused_" + label, outcome.get("refused") == STARTUP, outcome)
            if kept is not None:
                if path.read_bytes() != kept:
                    path.write_bytes(kept)
            elif path.exists():
                path.unlink()
            for folder in reversed(missing):   # Deepest first; only folders the case made, and only if empty.
                try:
                    folder.rmdir()
                except OSError:
                    pass
    if not sys.platform.startswith("linux"):
        check("sound_server_cases_linux_only", True)
        return
    jackdrc, client = home / ".jackdrc", config / "pulse" / "client.conf"
    kept = {path: path.read_bytes() for path in (jackdrc, client) if path.exists()}
    with tempfile.TemporaryDirectory(prefix="angelo-sound-server-") as folder:
        marker = Path(folder) / "started"
        program = _program(folder, marker)
        try:
            jackdrc.write_text("%s -d dummy\n" % program)
            client.parent.mkdir(parents=True, exist_ok=True)
            client.write_text("autospawn = yes\ndaemon-binary = %s\n" % program)
            for label, kind in DEVICES:
                outcome = run("import aud\naud.Device(type=KIND)", KIND=kind)
                check("sound_server_device_refused_in_script_" + label, outcome.get("refused") == PROGRAMS, outcome)
            for label, kind in DEVICES:
                try:
                    aud.Device(type=kind)   # Angelo's own code: may fail (no server running); must start none.
                except Exception:   # noqa: BLE001 - failing to connect is the expected outcome.
                    pass
                time.sleep(0.5)   # A forked server start would have written by now.
                started = marker.read_text() if marker.exists() else ""
                check("sound_server_not_started_later_" + label, started == "", started[:200])
                marker.unlink(missing_ok=True)
        finally:
            for path in (jackdrc, client):
                if path in kept:
                    path.write_bytes(kept[path])
                else:
                    path.unlink(missing_ok=True)
