# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The platform-neutral key-store contract: policy, the disable switch, backend selection and names.

No real Keychain, Credential Manager or keyring is opened: macOS selection
uses a stand-in class, and the Windows and Linux backends fail to open on a
host without advapi32 or libsecret exactly as they would on such a computer.
Flatpak detection reads sample ``/.flatpak-info`` files written to a
temporary folder, never the host's own.
"""

import ctypes
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import key_store, keychain_macos, secret_service_linux
from angelo_agent.key_store import (DISABLE_ENV, MEMORY_ONLY, KeyStoreError, KeyStoreUnavailable, decode_secret,
                                    encode_secret, open_store, store_policy, store_text)
from angelo_agent.secret_service_linux import SecretServiceStore, flatpak_bus_access
from test_native_secret_service import FakeLibSecret

BUS = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
SUPPORTED = ("darwin", "win32", "linux")


class PolicyTests(unittest.TestCase):
    def test_every_supported_platform_is_available_by_default(self):
        for platform in SUPPORTED:
            self.assertEqual(store_policy({}, platform, False), (True, ""))
        for platform in ("freebsd14", "sunos5", "emscripten"):
            self.assertEqual(store_policy({}, platform, False), (False, MEMORY_ONLY))

    def test_disable_switch_turns_off_every_backend(self):
        self.assertEqual(DISABLE_ENV, "ANGELO_DISABLE_KEYCHAIN")
        for platform in SUPPORTED:
            with self.subTest(platform=platform):
                available, reason = store_policy({DISABLE_ENV: "1"}, platform, False)
                self.assertFalse(available)
                self.assertIn(DISABLE_ENV, reason)
                self.assertTrue(reason.startswith(store_text(platform).title + " is turned off"))
                self.assertEqual(store_policy({DISABLE_ENV: "0"}, platform, False), (True, ""))
                self.assertEqual(store_policy({DISABLE_ENV: ""}, platform, False), (True, ""))

    def test_deprecated_switch_is_a_fallback_and_angelo_wins(self):
        for platform in SUPPORTED:
            for legacy in ("SENSEI_DISABLE_KEYCHAIN", "TOONLAB_DISABLE_KEYCHAIN"):
                with self.subTest(platform=platform, legacy=legacy):
                    available, reason = store_policy({legacy: "1"}, platform, False)
                    self.assertFalse(available)
                    self.assertIn(legacy, reason, "name the variable the person actually set")
                    self.assertTrue(store_policy({DISABLE_ENV: "0", legacy: "1"}, platform, False)[0])

    def test_background_mode_never_uses_a_store(self):
        for platform in SUPPORTED:
            available, reason = store_policy({}, platform, True)
            self.assertFalse(available)
            self.assertIn("background mode", reason)

    def test_macos_wording_is_unchanged(self):
        self.assertEqual(store_policy({DISABLE_ENV: "1"}, "darwin", False)[1],
                         "Keychain is turned off for this session (ANGELO_DISABLE_KEYCHAIN); keys stay in memory.")
        self.assertEqual(store_policy({}, "darwin", True)[1],
                         "Keychain isn't used in background mode; keys stay in memory.")


class SelectionTests(unittest.TestCase):
    def test_policy_refusals_never_load_a_library(self):
        with mock.patch.object(ctypes, "CDLL", side_effect=AssertionError("loaded a library")):
            for platform in SUPPORTED:
                self.assertIsNone(open_store({DISABLE_ENV: "1"}, platform, False)[0])
                self.assertIsNone(open_store(dict(BUS), platform, True)[0])

    def test_macos_selects_the_keychain_backend(self):
        with mock.patch.object(keychain_macos, "MacKeychain", side_effect=lambda: "keychain"):
            self.assertEqual(open_store({}, "darwin", False), ("keychain", ""))
        with mock.patch.object(keychain_macos, "MacKeychain", side_effect=OSError("no Security")):
            self.assertEqual(open_store({}, "darwin", False),
                             (None, "Keychain is unavailable on this Mac; keys stay in memory."))

    @unittest.skipIf(sys.platform == "win32", "advapi32 exists on Windows")
    def test_windows_without_advapi32_stays_memory_only(self):
        self.assertEqual(open_store({}, "win32", False),
                         (None, "Credential Manager is unavailable on this computer; keys stay in memory."))

    def test_linux_needs_a_session_bus_and_never_autolaunches_one(self):
        with mock.patch.object(secret_service_linux, "LibSecret", side_effect=AssertionError("loaded libsecret")):
            store, reason = open_store({"DISPLAY": ":0"}, "linux", False)
        self.assertIsNone(store)
        self.assertIn("desktop session bus (D-Bus)", reason)
        self.assertTrue(reason.endswith("; keys stay in memory."))

    def test_linux_session_bus_detection(self):
        self.assertTrue(secret_service_linux.session_bus_available(BUS))
        self.assertFalse(secret_service_linux.session_bus_available({}))
        self.assertFalse(secret_service_linux.session_bus_available({"XDG_RUNTIME_DIR": "/nonexistent-runtime"}))
        with mock.patch.object(secret_service_linux.os.path, "exists", return_value=True):
            self.assertTrue(secret_service_linux.session_bus_available({"XDG_RUNTIME_DIR": "/run/user/1000"}))

    def test_linux_without_libsecret_or_with_an_old_one_says_so(self):
        def missing(name):
            raise OSError("%s: cannot open shared object file" % name)
        with mock.patch.object(ctypes, "CDLL", side_effect=missing):
            store, reason = open_store(dict(BUS), "linux", False)
        self.assertIsNone(store)
        self.assertEqual(reason, "Your keyring can't be used: libsecret (libsecret-1.so.0) isn't installed; "
                                 "keys stay in memory.")
        with mock.patch.object(ctypes, "CDLL", side_effect=lambda name: object()):
            self.assertEqual(open_store(dict(BUS), "linux", False)[1],
                             "Your keyring can't be used: the installed libsecret is too old; keys stay in memory.")

    def test_unsupported_platform_stays_memory_only(self):
        self.assertEqual(open_store({}, "freebsd14", False), (None, MEMORY_ONLY))


FLATPAK_INFO = """[Application]
name=org.angelo.Angelo
runtime=runtime/org.freedesktop.Platform/x86_64/24.08

[Instance]
instance-id=2803468312
flatpak-version=1.16.1

[Context]
shared=network;ipc;
sockets=x11;wayland;pulseaudio;fallback-x11;
devices=dri;

[Environment]
PYTHONPATH=%(not interpolated)s

[Session Bus Policy]
org.freedesktop.Notifications=talk
"""
SECRETS = "org.freedesktop.secrets"


class FlatpakTests(unittest.TestCase):
    """The sandbox's own permissions (/.flatpak-info) decide whether the keyring service is reachable."""

    def info(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / ".flatpak-info"
        path.write_bytes(text if isinstance(text, bytes) else text.encode())
        return str(path)

    def test_outside_flatpak_there_is_no_sandbox_answer(self):
        self.assertIsNone(flatpak_bus_access(SECRETS, "/nonexistent/.flatpak-info"))
        self.assertEqual(secret_service_linux.FLATPAK_INFO, "/.flatpak-info", "the file libsecret checks")

    def test_bus_policy_decides_access(self):
        cases = {"": False, "org.freedesktop.secrets=talk\n": True, "org.freedesktop.secrets=own\n": True,
                 "org.freedesktop.secrets=see\n": False, "org.freedesktop.secrets=none\n": False,
                 "org.freedesktop.*=talk\n": True, "org.freedesktop.portal.*=talk\n": False,
                 "org.freedesktop.Secrets=talk\n": False, "org.freedesktop.secretsx=talk\n": False}
        for policy, allowed in cases.items():
            with self.subTest(policy=policy):
                self.assertIs(flatpak_bus_access(SECRETS, self.info(FLATPAK_INFO + policy)), allowed)
        full_bus = FLATPAK_INFO.replace("sockets=x11;", "sockets=session-bus;x11;")
        self.assertIs(flatpak_bus_access(SECRETS, self.info(full_bus)), True)
        no_policy = FLATPAK_INFO.split("[Session Bus Policy]")[0]
        self.assertIs(flatpak_bus_access(SECRETS, self.info(no_policy)), False)

    def test_an_unreadable_info_file_leaves_the_decision_to_the_probe(self):
        self.assertIs(flatpak_bus_access(SECRETS, self.info(b"\xff\xfe not a key file")), True)
        self.assertIs(flatpak_bus_access(SECRETS, self.info("no group header\n")), True)
        with tempfile.TemporaryDirectory() as directory:
            self.assertIs(flatpak_bus_access(SECRETS, directory), True)

    def test_a_sandbox_without_keyring_access_says_how_to_allow_it(self):
        environ = dict(BUS, FLATPAK_ID="org.angelo.Angelo")
        with mock.patch.object(secret_service_linux, "FLATPAK_INFO", self.info(FLATPAK_INFO)), \
                mock.patch.object(secret_service_linux, "LibSecret", side_effect=AssertionError("loaded libsecret")):
            store, reason = open_store(environ, "linux", False)
            self.assertIn("<app ID>", open_store(dict(BUS), "linux", False)[1])
        self.assertIsNone(store)
        self.assertEqual(reason, "This Flatpak has no access to your keyring service; to allow it, run: flatpak "
                                 "override --user --talk-name=org.freedesktop.secrets org.angelo.Angelo; "
                                 "keys stay in memory.")

    def test_a_sandbox_with_keyring_access_uses_the_usual_store(self):
        fake = FakeLibSecret()
        granted = self.info(FLATPAK_INFO + "org.freedesktop.secrets=talk\n")
        with mock.patch.object(secret_service_linux, "FLATPAK_INFO", granted), \
                mock.patch.object(ctypes, "CDLL", fake.load):
            store, reason = open_store(dict(BUS, FLATPAK_ID="org.angelo.Angelo"), "linux", False)
        self.assertEqual((type(store), reason), (SecretServiceStore, ""))

    def test_flatpak_id_alone_is_not_a_sandbox(self):
        fake = FakeLibSecret()
        with mock.patch.object(secret_service_linux, "FLATPAK_INFO", "/nonexistent/.flatpak-info"), \
                mock.patch.object(ctypes, "CDLL", fake.load):
            store, reason = open_store(dict(BUS, FLATPAK_ID="org.angelo.Angelo"), "linux", False)
        self.assertEqual((type(store), reason), (SecretServiceStore, ""))


class TextAndSecretTests(unittest.TestCase):
    def test_store_names(self):
        self.assertEqual(store_text("darwin").remember, "Remember in Keychain")
        self.assertEqual(store_text("win32").remember, "Remember in Credential Manager")
        self.assertEqual(store_text("linux").remember, "Remember in your keyring")
        self.assertEqual(store_text("darwin").load_label, "Load Saved Key from Keychain")
        self.assertEqual(store_text("darwin").location, "your macOS login Keychain")
        self.assertEqual(store_text("linux").load_label, "Load Saved Key from your keyring")
        self.assertIs(store_text(), store_text(sys.platform))

    def test_encode_refuses_empty_and_oversized_secrets_without_echo(self):
        self.assertEqual(encode_secret("kéy-🔑", "Store"), "kéy-🔑".encode("utf-8"))
        with self.assertRaises(KeyStoreError) as caught:
            encode_secret("", "Store")
        self.assertEqual(caught.exception.kind, "failed")
        secret = "x" * 11
        with self.assertRaises(KeyStoreError) as caught:
            encode_secret(secret, "Store", limit=10)
        self.assertEqual((caught.exception.kind, caught.exception.code), ("too_long", "11 of 10 bytes"))
        self.assertNotIn(secret, str(caught.exception) + repr(caught.exception))
        self.assertEqual(len(encode_secret("x" * 10, "Store", limit=10)), 10)

    def test_decode_never_echoes_bytes(self):
        self.assertEqual(decode_secret("kéy".encode("utf-8"), "Store"), "kéy")
        with self.assertRaises(KeyStoreError) as caught:
            decode_secret(b"\xff\xfe" + b"secret-material", "Store")
        self.assertNotIn("secret-material", str(caught.exception) + repr(caught.exception))

    def test_unavailable_errors_carry_a_person_facing_message(self):
        error = KeyStoreUnavailable("No vault", "Windows error 1312")
        self.assertEqual((error.kind, error.message, str(error)), ("unavailable", "No vault",
                                                                   "No vault (Windows error 1312)"))
        self.assertEqual(key_store.KINDS, {"approval", "denied", "unavailable", "failed", "timeout", "too_long"})


if __name__ == "__main__":
    unittest.main()
