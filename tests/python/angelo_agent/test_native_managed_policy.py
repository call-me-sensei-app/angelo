# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The managed policy (managed_policy.py) and where it is enforced; the team lock, text and console reads.

Policy sources (macOS managed preferences, Windows HKLM/HKCU policy values
through a fake ``winreg``, Linux JSON, the test file), their precedence, the
restrictive reading of unusable values, and enforcement in endpoint origins,
connection lookup, the connection store, runtime launch, voice and the local
CLI host. Also ``team_lock`` across real processes, ``display_text``
translation at display time, ``team_reads`` parsing and the status-bar item.
"""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import (connection_access, display_text, managed_policy, team_endpoints, team_lock,  # noqa: E402
                          team_reads, team_runtime, ui_text)
from angelo_agent.connections import ConnectionStore  # noqa: E402
from angelo_agent.managed_policy import INVALID, Policy  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.team_view import AccountView  # noqa: E402

ORG = "6b0f3c1e-2d4a-4c8e-9f10-1a2b3c4d5e6f"


class PolicyCase(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)

    def use(self, **entries):
        policy = managed_policy.combine([("test", entries)])
        managed_policy.reset(policy)
        return policy


class ParsingTests(PolicyCase):
    def test_unset_keys_keep_angelos_defaults(self):
        policy = managed_policy.combine([])
        self.assertEqual(policy, Policy())
        self.assertFalse(policy.managed)
        self.assertFalse(policy.team_only)

    def test_values_of_every_kind(self):
        policy = managed_policy.combine([("profile", {
            "ConsoleURL": "https://console.northwind.example/", "GatewayURL": "https://gateway.northwind.example",
            "RequireTeamSignIn": True, "Organization": ORG.upper(), "EmailDomains": "northwind.example, @NW.example",
            "AllowPersonalConnections": 0, "AllowLocalCLIAccess": "false", "AllowVoice": "no",
            "AllowCollaborators": False, "AdminContact": "it-help@northwind.example", "Unknown": 1})])
        self.assertEqual((policy.console_url, policy.gateway_url), ("https://console.northwind.example",
                                                                    "https://gateway.northwind.example"))
        self.assertTrue(policy.require_team_sign_in and policy.team_only)
        self.assertEqual(policy.organization, ORG)
        self.assertEqual(policy.email_domains, ("northwind.example", "nw.example"))
        self.assertFalse(policy.allow_personal_connections or policy.allow_local_cli_access or policy.allow_voice
                         or policy.allow_collaborators)
        self.assertEqual(policy.admin_contact, "it-help@northwind.example")
        self.assertEqual(policy.problems, ())
        self.assertNotIn("Unknown", policy.keys)

    def test_unusable_values_apply_restrictively_and_are_reported(self):
        policy = managed_policy.combine([("profile", {
            "AllowPersonalConnections": "sometimes", "RequireTeamSignIn": "maybe", "ConsoleURL": "http://console.example",
            "Organization": "northwind", "EmailDomains": "not a domain", "AdminContact": "", "ExtraCACertificates": "relative.pem"})])
        self.assertFalse(policy.allow_personal_connections)
        self.assertTrue(policy.require_team_sign_in)
        self.assertEqual((policy.console_url, policy.organization, policy.email_domains), (INVALID, INVALID, (INVALID,)))
        self.assertEqual(set(policy.problems), {"AllowPersonalConnections", "RequireTeamSignIn", "ConsoleURL",
                                                "Organization", "EmailDomains", "AdminContact", "ExtraCACertificates"})
        problem = managed_policy.sign_in_problem(policy)
        self.assertIn("ConsoleURL, Organization, EmailDomains", problem)
        self.assertIsNone(managed_policy.admit(Policy(), ORG, "a@b.example"))
        self.assertEqual(managed_policy.admit(policy, ORG, "a@b.example"), problem)

    def test_the_update_and_crash_report_switches(self):
        self.assertFalse(Policy().disable_update_check or Policy().disable_crash_reporting)
        policy = managed_policy.combine([("profile", {"DisableUpdateCheck": "yes", "DisableCrashReporting": 1})])
        self.assertTrue(policy.disable_update_check and policy.disable_crash_reporting)
        self.assertEqual(policy.problems, ())
        self.assertTrue(policy.locked("DisableUpdateCheck") and not policy.team_only)
        policy = managed_policy.combine([("profile", {"DisableUpdateCheck": False})])
        self.assertFalse(policy.disable_update_check)
        self.assertTrue(policy.managed)
        # A typo reads as "disabled" and is reported, like every unusable value: it can't switch the lock off.
        policy = managed_policy.combine([("profile", {"DisableUpdateCheck": "nope", "DisableCrashReporting": [1]})])
        self.assertTrue(policy.disable_update_check and policy.disable_crash_reporting)
        self.assertEqual(set(policy.problems), {"DisableUpdateCheck", "DisableCrashReporting"})
        self.assertTrue(managed_policy.combine([("device", {"DisableUpdateCheck": True}),
                                                ("test file", {"DisableUpdateCheck": False})]).disable_update_check)

    def test_an_update_feed_mirror_must_be_an_https_link_or_the_check_stops(self):
        mirror = "https://updates.northwind.example/angelo/stable.json"
        policy = managed_policy.combine([("profile", {"UpdateFeedURL": " %s " % mirror})])
        self.assertEqual((policy.update_feed_url, policy.problems), (mirror, ()))
        self.assertIsNone(managed_policy.sign_in_problem(policy), "not a sign-in setting")
        for bad in ("http://updates.northwind.example/stable.json", "https://user:pw@updates.example/stable.json",
                    "https://updates.example/stable.json?x=1", "https://updates.example:8443/stable.json", "updates.example",
                    "https://updates.example/a b.json", 5, ""):
            with self.subTest(bad=bad):
                policy = managed_policy.combine([("profile", {"UpdateFeedURL": bad})])
                self.assertEqual((policy.update_feed_url, policy.problems), (managed_policy.INVALID, ("UpdateFeedURL",)))
        self.assertEqual(Policy().update_feed_url, "")

    def test_a_higher_source_wins_key_by_key(self):
        policy = managed_policy.combine([("device", {"AllowVoice": False}),
                                         ("user", {"AllowVoice": True, "AllowCollaborators": False}),
                                         ("test file", {"AllowCollaborators": True, "AllowPersonalConnections": False})])
        self.assertFalse(policy.allow_voice)
        self.assertFalse(policy.allow_collaborators)
        self.assertFalse(policy.allow_personal_connections)
        self.assertEqual(policy.sources, ("device", "user", "test file"))


class OrganizationTests(PolicyCase):
    def test_the_stricter_value_wins_and_only_the_machine_pins_where_to_sign_in(self):
        machine = managed_policy.combine([("profile", {"AllowVoice": False, "AdminContact": "IT desk",
                                                      "ConsoleURL": "https://console.northwind.example"})])
        org = {"allowPersonalConnections": False, "allowLocalCliAccess": True, "allowVoice": True,
               "allowCollaborators": True, "adminContact": "admins@northwind.example"}
        policy = managed_policy.with_organization(machine, org)
        self.assertFalse(policy.allow_personal_connections, "the organization turned it off")
        self.assertFalse(policy.allow_voice, "the organization can't turn on what the machine turned off")
        self.assertEqual(policy.admin_contact, "IT desk", "the machine's contact first")
        self.assertEqual(policy.console_url, "https://console.northwind.example")
        self.assertEqual(policy.organization_keys, frozenset({"allowPersonalConnections"}))
        self.assertTrue(managed_policy.with_organization(Policy(), {"allowVoice": False}).managed)
        self.assertEqual(managed_policy.with_organization(Policy(), dict(org, allowPersonalConnections=True,
                                                                         adminContact="")).admin_contact, "")
        managed_policy.reset(machine)
        managed_policy.set_organization_source(lambda: org)
        self.assertFalse(managed_policy.current().allow_personal_connections)
        managed_policy.set_organization_source(lambda: None)
        self.assertEqual(managed_policy.current(), machine)

    def test_the_token_answers_policy_is_read_strictly(self):
        from angelo_agent.team_view import org_policy
        self.assertIsNone(org_policy(None))
        self.assertIsNone(org_policy([]))
        self.assertEqual(org_policy({}), {"allowPersonalConnections": True, "allowLocalCliAccess": True,
                                          "allowVoice": True, "allowCollaborators": True, "adminContact": ""})
        odd = org_policy({"allowPersonalConnections": "false", "allowVoice": 0, "adminContact": "x\ny"})
        self.assertEqual((odd["allowPersonalConnections"], odd["allowVoice"], odd["adminContact"]), (False, False, ""))


class SourceTests(PolicyCase):
    def test_macos_device_profile_wins_over_the_user_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            device = Path(folder, managed_policy.DOMAIN + ".plist")
            user = Path(folder, "mika", managed_policy.DOMAIN + ".plist")
            user.parent.mkdir()
            device.write_bytes(plistlib.dumps({"AllowVoice": False}))
            user.write_bytes(plistlib.dumps({"AllowVoice": True, "AdminContact": "IT desk"}))
            with patch.object(managed_policy, "MAC_DIRECTORY", folder):
                sources = managed_policy.mac_sources(user="mika")
                policy = managed_policy.load("darwin", {}, mac=lambda: sources)
            self.assertEqual([path for path, _ in sources], [str(device), str(user)])
            self.assertFalse(policy.allow_voice)
            self.assertEqual(policy.admin_contact, "IT desk")
            with patch.object(managed_policy, "MAC_DIRECTORY", folder):
                self.assertEqual(managed_policy.mac_sources(user="../etc"), [(str(device), {"AllowVoice": False})])

    def test_windows_machine_policy_wins_over_user_policy(self):
        class Registry:
            HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "HKLM", "HKCU"
            KEY_READ, KEY_WOW64_64KEY = 1, 2
            REG_SZ, REG_EXPAND_SZ, REG_DWORD, REG_MULTI_SZ, REG_BINARY = 1, 2, 4, 7, 3
            values = {"HKLM": [("AllowPersonalConnections", 0, 4), ("EmailDomains", ["northwind.example"], 7),
                               ("Ignored", b"\x00", 3)],
                      "HKCU": [("AllowPersonalConnections", 1, 4), ("AdminContact", "x1234", 1)]}
            opened = []

            def OpenKey(self, hive, path, reserved, access):  # noqa: N802 - winreg's names.
                self.opened.append((hive, path, access))
                return hive

            def EnumValue(self, key, index):  # noqa: N802
                try:
                    return self.values[key][index]
                except IndexError:
                    raise OSError("no more") from None

            def CloseKey(self, key):  # noqa: N802
                pass
        registry = Registry()
        policy = managed_policy.load("win32", {}, windows=lambda: managed_policy.windows_sources(registry))
        self.assertEqual(registry.opened[0], ("HKLM", r"SOFTWARE\Policies\Hyperbond\Angelo", 3))
        self.assertFalse(policy.allow_personal_connections, "HKLM wins")
        self.assertEqual((policy.email_domains, policy.admin_contact), (("northwind.example",), "x1234"))
        self.assertEqual(policy.sources[0], r"HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Hyperbond\Angelo")

        class Missing(Registry):
            def OpenKey(self, *args):  # noqa: N802
                raise FileNotFoundError(2, "absent")
        self.assertEqual(managed_policy.load("win32", {}, windows=lambda: managed_policy.windows_sources(Missing())),
                         Policy())

    def test_windows_expands_an_expandable_string_and_only_that(self):
        """REG_EXPAND_SZ (Group Policy's expandable string) is expanded as Windows does; REG_SZ stays literal."""
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "roots.pem").write_text("-----BEGIN CERTIFICATE-----\n")

            class Registry:
                HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "HKLM", "HKCU"
                KEY_READ = 1
                REG_SZ, REG_EXPAND_SZ, REG_DWORD, REG_MULTI_SZ = 1, 2, 4, 7
                values = {"HKLM": [("ExtraCACertificates", "%ProgramData%/roots.pem", 2)], "HKCU": []}

                def OpenKey(self, hive, path, reserved, access):  # noqa: N802 - winreg's names.
                    return hive

                def EnumValue(self, key, index):  # noqa: N802
                    try:
                        return self.values[key][index]
                    except IndexError:
                        raise OSError("no more") from None

                def CloseKey(self, key):  # noqa: N802
                    pass

                @staticmethod
                def ExpandEnvironmentStrings(value):  # noqa: N802
                    return value.replace("%ProgramData%", folder)

            registry = Registry()
            policy = managed_policy.load("win32", {}, windows=lambda: managed_policy.windows_sources(registry))
            self.assertEqual((policy.extra_ca_certificates, policy.problems),
                             (folder + "/roots.pem", ()))   # The expanded value as written (Windows accepts "/").
            registry.values = {"HKLM": [("ExtraCACertificates", "%ProgramData%/roots.pem", 1)], "HKCU": []}
            policy = managed_policy.load("win32", {}, windows=lambda: managed_policy.windows_sources(registry))
            self.assertEqual(policy.extra_ca_certificates, "")
            self.assertIn("ExtraCACertificates", policy.problems, "a REG_SZ is literal, as on Windows")

    def test_linux_file_and_the_test_file_which_never_overrides_a_managed_key(self):
        with tempfile.TemporaryDirectory() as folder:
            test_file = Path(folder, "policy.json")
            test_file.write_text('{"AllowVoice": true, "AllowCollaborators": false}')
            reads = {managed_policy.LINUX_FILE: {"AllowVoice": False}}
            policy = managed_policy.load("linux", {"ANGELO_POLICY_FILE": str(test_file)},
                                         read_json=lambda path: reads.get(path) or managed_policy._read_json(path))
            self.assertFalse(policy.allow_voice)
            self.assertFalse(policy.allow_collaborators)
            self.assertEqual(managed_policy.load("linux", {}, read_json=lambda path: None), Policy())


class EnforcementTests(PolicyCase):
    def test_a_managed_console_wins_over_the_environment_and_an_invalid_one_stops_sign_in(self):
        environ = {"ANGELO_CONSOLE_URL": "http://localhost:5280"}
        self.assertEqual(team_endpoints.console_origin(environ).url, "http://localhost:5280")
        self.use(ConsoleURL="https://console.northwind.example")
        self.assertEqual(team_endpoints.console_origin(environ).url, "https://console.northwind.example")
        self.use(ConsoleURL="ftp://nope")
        with self.assertRaisesRegex(ValueError, "invalid ConsoleURL"):
            team_endpoints.console_origin(environ)

    def test_personal_connections_disappear_from_every_lookup_and_come_back(self):
        store = ConnectionStore(tempfile.mkdtemp())
        personal = store.add("ANTHROPIC", "API_KEY")
        team = {"id": "team-1", "mode": "claude_team"}
        signed_in = {"value": False}
        fake_ui = SimpleNamespace(connections=lambda: store)
        fake_team = SimpleNamespace(records=lambda: {"team-1": team}, find=lambda i: team if i == "team-1" else None,
                                    signed_in=lambda: signed_in["value"])
        with patch.object(connection_access, "_ui", lambda: fake_ui), patch.object(connection_access, "_team", lambda: fake_team):
            self.assertEqual(set(connection_access.connection_records()), {personal["id"], "team-1"})
            self.use(AllowPersonalConnections=False)
            self.assertEqual(set(connection_access.connection_records()), {"team-1"})
            self.assertIsNone(connection_access.find_connection(personal["id"]))
            self.assertIs(connection_access.find_connection("team-1"), team)
            self.use(RequireTeamSignIn=True)
            self.assertIsNone(connection_access.find_connection(personal["id"]), "not until the team sign-in")
            signed_in["value"] = True
            self.assertEqual(connection_access.find_connection(personal["id"]), personal)
        self.assertIn(personal["id"], store.records, "the stored connection itself is kept")

    def test_the_store_refuses_new_personal_connections(self):
        store = ConnectionStore(tempfile.mkdtemp())
        self.use(AllowPersonalConnections=False)
        with self.assertRaisesRegex(ValueError, "allows only its Angelo team connections"):
            store.add("OPENAI", "SUBSCRIPTION")

    def test_a_personal_runtime_never_launches_and_claude_gets_the_extra_roots(self):
        self.use(AllowPersonalConnections=False)
        with self.assertRaisesRegex(RpcError, "allows only its Angelo team"):
            team_runtime.apply_policy({}, "claude_api")
        self.assertEqual(team_runtime.apply_policy({}, "claude_team"), {})
        pem = Path(tempfile.mkdtemp(), "root.pem")
        pem.write_text("-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n")
        self.use(ExtraCACertificates=str(pem))
        self.assertEqual(team_runtime.apply_policy({}, "claude_api"), {"NODE_EXTRA_CA_CERTS": str(pem)})
        self.assertEqual(team_runtime.apply_policy({"NODE_EXTRA_CA_CERTS": "/own.pem"}, "claude_team"),
                         {"NODE_EXTRA_CA_CERTS": "/own.pem"})
        self.assertEqual(team_runtime.apply_policy({}, "codex_team"), {})

    def test_collaborators_voice_and_local_cli(self):
        from angelo_agent import external_host, voice
        self.assertTrue(connection_access.collaborators_allowed())
        self.use(AllowCollaborators=False, AllowVoice=False, AllowLocalCLIAccess=False)
        self.assertFalse(connection_access.collaborators_allowed())
        self.assertIn("Voice is turned off by your organization", voice.availability())
        host = external_host.ExternalHost.__new__(external_host.ExternalHost)
        host.environ, host.env = {}, SimpleNamespace(access_requested=lambda: True)
        self.assertEqual(host.wanted(), (False, "Turned off by your organization."))

    def test_admission_by_organization_and_email_domain(self):
        policy = self.use(Organization=ORG, EmailDomains=["northwind.example"])
        self.assertIsNone(managed_policy.admit(policy, ORG, "mika@northwind.example"))
        self.assertIn("different Angelo team", managed_policy.admit(policy, "other", "mika@northwind.example"))
        self.assertIn("@northwind.example", managed_policy.admit(policy, ORG, "mika@gmail.example"))
        self.assertIn("work account", managed_policy.admit(policy, ORG, "mika"))

    def test_extra_roots_are_added_and_certifi_fills_an_empty_store(self):
        import ssl
        from angelo_agent import team_http
        folder = tempfile.mkdtemp()
        pem = Path(folder, "root.pem")
        subprocess.run([openssl(), "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Angelo test root",
                        "-keyout", str(Path(folder, "key.pem")), "-out", str(pem)], check=True, capture_output=True)
        empty = lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # noqa: E731 - Blender's Python on macOS: no roots.
        with patch.object(team_http.ssl, "create_default_context", empty), \
                patch.dict(sys.modules, {"certifi": SimpleNamespace(where=lambda: str(pem))}):
            self.assertEqual(team_http.tls_context(Policy()).cert_store_stats()["x509_ca"], 1, "certifi's roots")
        policy = managed_policy.combine([("profile", {"ExtraCACertificates": str(pem)})])
        self.assertEqual(policy.extra_ca_certificates, str(pem))
        with patch.object(team_http.ssl, "create_default_context", empty), patch.dict(sys.modules, {"certifi": None}):
            self.assertEqual(team_http.tls_context(policy).cert_store_stats()["x509_ca"], 1, "the extra root is added")
        context = team_http.tls_context(policy)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


def openssl():
    """The openssl CLI: on PATH, or the copy Git for Windows ships (usr\\bin beside its cmd\\git.exe)."""
    found = shutil.which("openssl")
    git = shutil.which("git")
    if not found and git:
        candidate = Path(git).resolve().parents[1] / "usr" / "bin" / "openssl.exe"
        found = str(candidate) if candidate.is_file() else None
    if not found:
        raise unittest.SkipTest("openssl is not installed")
    return found


class TeamLockTests(unittest.TestCase):
    def test_another_process_holding_the_lock_times_out_and_then_releases(self):
        folder = tempfile.mkdtemp()
        holder = subprocess.Popen([sys.executable, "-c", (
            "import sys, time; sys.path.insert(0, %r)\n"
            "from angelo_agent import team_lock\n"
            "with team_lock.held(%r):\n    print('held', flush=True); time.sleep(1.5)\n")
            % (str(ROOT / "scripts/addons_core"), folder)], stdout=subprocess.PIPE, text=True)
        self.assertEqual(holder.stdout.readline().strip(), "held")
        started = time.monotonic()
        with self.assertRaises(team_lock.LockTimeout):
            with team_lock.held(folder, 0.3):
                pass
        self.assertLess(time.monotonic() - started, 1.0)
        with team_lock.held(folder, 10):
            self.assertGreater(time.monotonic() - started, 1.0, "acquired once the other process let go")
        holder.wait(10)

    def test_a_crashed_holder_never_leaves_the_lock_taken(self):
        folder = tempfile.mkdtemp()
        subprocess.run([sys.executable, "-c", (
            "import os, sys; sys.path.insert(0, %r)\n"
            "from angelo_agent import team_lock\n"
            "cm = team_lock.held(%r); cm.__enter__(); os._exit(3)\n") % (str(ROOT / "scripts/addons_core"), folder)])
        with team_lock.held(folder, 1):
            pass


class TextTests(unittest.TestCase):
    def test_text_is_english_and_is_translated_where_shown(self):
        text = display_text.Text(managed_policy.CONTACT_ADMIN_AT, contact="IT desk")
        self.assertEqual(text, "Contact your admin for access: IT desk")
        catalog = {managed_policy.CONTACT_ADMIN_AT: "Wenden Sie sich an Ihren Administrator: {contact}",
                   managed_policy.CONTACT_ADMIN: "Wenden Sie sich an Ihren Administrator."}
        with patch.dict(ui_text._pgettext, {"iface": lambda message, context=None: catalog.get(message, message)}):
            self.assertEqual(display_text.shown(text), "Wenden Sie sich an Ihren Administrator: IT desk")
            self.assertEqual(display_text.shown(managed_policy.CONTACT_ADMIN), "Wenden Sie sich an Ihren Administrator.")
            self.assertEqual(display_text.shown(display_text.Text("{inner}", inner=display_text.Text(managed_policy.CONTACT_ADMIN))),
                             "Wenden Sie sich an Ihren Administrator.")
        self.assertEqual(display_text.shown(""), "")

    def test_every_team_and_policy_template_has_named_placeholders_only(self):
        import re
        for module in (managed_policy, __import__("angelo_agent.team_errors", fromlist=["x"])):
            for name, value in vars(module).items():
                if name.isupper() and isinstance(value, str) and "{" in value:
                    with self.subTest(template=name):
                        self.assertFalse(re.search(r"\{\}|\{\d+\}|%s|%d", value), "a translator must be able to reorder")


class ReadsAndStatusTests(unittest.TestCase):
    def test_console_answers_are_read_defensively(self):
        self.assertEqual(team_reads.providers({"data": {"providers": ["openai", "anthropic", "x", 1]}}),
                         ("anthropic", "openai"))
        self.assertEqual(team_reads.providers({"data": {"providers": "openai"}}), ())
        usage = team_reads.usage({"data": {"thisPeriod": {"tokens": 1250000, "agentTasks": 42, "generations": 6,
                                                          "activeDays": 7}, "stats": {"currentStreakDays": 3,
                                                                                      "lifetimeTokens": True}}})
        self.assertEqual(usage, {"tokens": 1250000, "agent_tasks": 42, "generations": 6, "active_days": 7,
                                 "streak": 3, "lifetime_tokens": 0})
        self.assertIsNone(team_reads.usage({"data": {}}))
        self.assertEqual(team_reads.usage_path("a/b", "c"), "/api/orgs/a%2Fb/usage/people/c")
        self.assertIsNone(team_reads.grant_key({"data": {"key": "agk_short", "expires_in": 43200}}))
        self.assertEqual(team_reads.grant_key({"data": {"key": "agk_" + "k" * 43, "expires_in": 43200}}),
                         ("agk_" + "k" * 43, 43200))

    def test_the_status_bar_item(self):
        bpy = ModuleType("bpy")
        bpy.types = SimpleNamespace(Menu=object, Operator=object, Panel=object)
        bpy.props = SimpleNamespace(EnumProperty=lambda **_: None, StringProperty=lambda **_: None)
        bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
        modules = {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props}
        with patch.dict(sys.modules, modules):
            for name in ("angelo_agent.team_status_bar", "angelo_agent.team_account_ui", "angelo_agent.preferences_ui"):
                sys.modules.pop(name, None)
            from angelo_agent import team_status_bar
        signed_in = AccountView(status="signed_in", org_name="Northwind Studio", email="mika@northwind.example", seat=True)
        self.assertEqual(team_status_bar.status_item(signed_in, Policy()), ("Northwind Studio · mika", "COMMUNITY"))
        self.assertEqual(team_status_bar.status_item(AccountView(status="signed_in", org_name="N", email="m@x",
                                                                 seat=False), Policy())[1], "ERROR")
        self.assertEqual(team_status_bar.status_item(AccountView(), Policy()), ("Sign in", "USER"))
        team_only = Policy(allow_personal_connections=False)
        self.assertEqual(team_status_bar.status_item(AccountView(), team_only), ("Sign in", "ERROR"))
        self.assertEqual(team_status_bar.status_item(AccountView(status="signing_in"), Policy()), ("Signing in…", "TIME"))
        self.assertEqual(team_status_bar.status_item(None, Policy(), unavailable=True), ("", "NONE"))
        self.assertEqual(team_status_bar.status_item(None, team_only, unavailable=True)[1], "ERROR")
        self.assertEqual([team_status_bar.short_number(n) for n in (999, 1250, 1250000, 3_000_000_000)],
                         ["999", "1.2K", "1.2M", "3B"])


if __name__ == "__main__":
    unittest.main()
