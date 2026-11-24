# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""audaspace never starts a sound server, and a confined script never opens an output device.

libjack without JackNoStartServer runs the first line of ``~/.jackdrc``; PulseAudio without PA_CONTEXT_NOAUTOSPAWN
runs the ``daemon-binary`` its ``client.conf`` names: both files a script may write. Whether it holds at run time is
``test_blender_script_confinement``'s (``sound_server_*``). Reads sources; dependency-free.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AUD = ROOT / "extern/audaspace/plugins"


class SoundServers(unittest.TestCase):
    def test_jack_never_starts_a_server(self):
        text = (AUD / "jack/JackDevice.cpp").read_text(encoding="utf-8")
        self.assertIn("jack_options_t options = JackNoStartServer;", text)
        self.assertNotIn("JackNullOption", re.sub(r"//[^\n]*", "", text))

    def test_every_pulseaudio_connect_refuses_autospawn(self):
        for path in sorted(AUD.rglob("*.cpp")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "pa_context_connect(" in line and "SYMBOL" not in line:
                    with self.subTest(path=path.name):
                        self.assertIn("PA_CONTEXT_NOAUTOSPAWN", line)

    def test_audio_configuration_is_never_written_by_a_script(self):
        """The audio stack's per-user configuration is on os_startup's never-write list on every platform
        (break-it-confine-fixes-b31e34840ca MUST-3)."""
        import sys
        sys.path.insert(0, str(ROOT / "scripts/addons_core"))
        from angelo_agent.script_confinement_blender import os_startup
        # The list is joined with the host's separator (os.path.join): compared with `/` on every host, so the same
        # check holds on Linux, macOS and Windows (on Windows `/h` and `.asoundrc` join with a backslash).
        def joined(paths):
            return {path.replace("\\", "/") for path in paths}
        linux = joined(os_startup(environ={}, platform="linux", home="/h"))
        self.assertIn("/x/alsa", joined(os_startup(environ={"XDG_CONFIG_HOME": "/x"}, platform="linux", home="/h")))
        for path in ("/h/.asoundrc", "/h/.asoundrc.asoundconf", "/h/.alsoftrc", "/h/.jackdrc", "/h/.config/alsoft.conf",
                     "/h/.config/pulse", "/h/.config/pipewire", "/h/.config/alsa", "/h/.pulse"):
            self.assertIn(path, linux)
        self.assertIn("/h/Library/Preferences/alsoft.conf", joined(os_startup(environ={}, platform="darwin", home="/h")))
        windows = os_startup(environ={"APPDATA": "C:\\A"}, platform="win32", home="C:\\H")
        self.assertTrue(any(path.endswith("alsoft.ini") for path in windows), windows)

    def test_every_platform_outside_windows_guards_the_same_audio_configuration(self):
        """A library reads the same per-user places on macOS as on Linux, resolved the same way ($XDG_CONFIG_HOME,
        else ~/.config): OpenAL Soft's alconfig.cpp reads $XDG_CONFIG_HOME/alsoft.conf on macOS too. macOS listed
        only the home-folder files, so a script's write to $XDG_CONFIG_HOME/alsoft.conf went through (iso-mac's macOS
        run of test_blender_script_confinement: audio_config_write_refused_{pulse,pipewire,alsoft,alsa_xdg}). Only
        what one platform alone runs at login (Linux's autostart and systemd user folders) differs."""
        import sys
        sys.path.insert(0, str(ROOT / "scripts/addons_core"))
        from angelo_agent.script_confinement_blender import os_startup

        def joined(paths):
            return {path.replace("\\", "/") for path in paths}
        for environ, config in (({}, "/h/.config"), ({"XDG_CONFIG_HOME": "/x"}, "/x")):
            linux = joined(os_startup(environ=environ, platform="linux", home="/h"))
            login_only = {config + "/autostart", config + "/systemd/user"}
            self.assertLessEqual(login_only, linux)
            for platform in ("darwin", "freebsd14"):
                with self.subTest(platform=platform, environ=environ):
                    other = joined(os_startup(environ=environ, platform=platform, home="/h"))
                    self.assertLessEqual(linux - login_only, other)
                    for name in ("alsoft.conf", "pulse", "pipewire", "alsa"):
                        self.assertIn(config + "/" + name, other)

    def test_a_confined_script_opens_no_output_device(self):
        table = (ROOT / "source/blender/python/intern/bpy_angelo_guard_confine_pipeline_table.cc").read_text(
            encoding="utf-8")
        self.assertIn('G("aud", "Device", "__new__", N, NONE, NONE, NONE, RefuseProgram),', table)
        special = (ROOT / "source/blender/python/intern/bpy_angelo_guard_confine_pipeline_special.cc").read_text(
            encoding="utf-8")
        self.assertIn("case Special::RefuseProgram:\n      return refuse(PROGRAMS, gate.name);", special)


if __name__ == "__main__":
    unittest.main()
