# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Saved BYOK keys: the macOS Keychain backend and the shared worker, on any host OS through fakes.

The Security-framework binding itself is exercised on macOS by the opt-in
LIVE suite ``test_blender_keychain_live.py``; these tests pin the query
shapes, the prompt policy, threading, deadlines and failure reporting. The
Windows and Linux backends have their own suites
(``test_native_credential_manager.py``, ``test_native_secret_service.py``) and
the platform selector is in ``test_native_key_store.py``.
"""

import sys
import threading
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.key_store import KeyStoreError
from angelo_agent.keychain_macos import COMMENT, SERVICE, Const, KeychainError, MacKeychain
from angelo_agent.saved_keys import SavedKeys, account_name, profile_id

KEY = "test-key-not-real-0001"
OTHER = "test-key-not-real-0002"
DATA, SERVICE_KEY, ACCOUNT_KEY = Const("kSecValueData"), Const("kSecAttrService"), Const("kSecAttrAccount")


class FakeFramework:
    """SecItem semantics on a dict, with the owner behavior measured on macOS 26.3.

    An item belongs to the build that created it. Another build, with prompts
    disallowed, cannot read it (errSecAuthFailed) or delete it
    (errSecInvalidOwnerEdit) but may overwrite its secret in place. With
    prompts allowed, ``approve`` decides the person's answer to the prompt.
    """

    def __init__(self):
        self.items, self.calls, self.interaction = {}, [], True
        self.update_status, self.add_status, self.build, self.approve = None, None, "build-1", True

    def _key(self, query):
        assert query[Const("kSecClass")] == Const("kSecClassGenericPassword")
        return query[SERVICE_KEY], query[ACCOUNT_KEY]

    def _log(self, name, *dictionaries):
        self.calls.append((name, self.interaction, [{k: v for k, v in d.items() if k != DATA} for d in dictionaries]))

    def add(self, attributes):
        self._log("add", attributes)
        if self.add_status is not None:
            return self.add_status
        key = self._key(attributes)
        if key in self.items:
            return -25299
        self.items[key] = dict(attributes, owner=self.build)
        return 0

    def _owner_check(self, key):
        if self.items[key]["owner"] == self.build:
            return 0
        if not self.interaction:
            return None  # The caller decides which no-prompt status applies.
        if self.approve:
            self.items[key]["owner"] = self.build  # "Always Allow" trusts this build.
            return 0
        return -128

    def update(self, query, attributes):
        self._log("update", query, attributes)
        if self.update_status is not None:
            return self.update_status
        if self._key(query) not in self.items:
            return -25300
        self.items[self._key(query)].update(attributes)
        return 0

    def copy_data(self, query):
        self._log("copy", query)
        assert query[Const("kSecReturnData")] is True and query[Const("kSecMatchLimit")] == Const("kSecMatchLimitOne")
        key = self._key(query)
        if key not in self.items:
            return -25300, None
        status = self._owner_check(key)
        if status != 0:
            return (-25293 if status is None else status), None
        return 0, self.items[key][DATA]

    def delete(self, query):
        self._log("delete", query)
        key = self._key(query)
        if key not in self.items:
            return -25300
        status = self._owner_check(key)
        if status != 0:
            return -25244 if status is None else status
        del self.items[key]
        return 0

    def interaction_allowed(self):
        return self.interaction

    def set_interaction_allowed(self, allowed):
        self.interaction = allowed


class MacKeychainTests(unittest.TestCase):
    def setUp(self):
        self.framework = FakeFramework()
        self.keychain = MacKeychain(self.framework)

    def test_round_trip_and_item_identity(self):
        self.assertIsNone(self.keychain.read("a@p"))
        self.keychain.write("a@p", KEY, "Angelo API key · Grok API")
        item = self.framework.items[(SERVICE, "a@p")]
        self.assertEqual(item[DATA], KEY.encode())
        self.assertEqual(item[Const("kSecAttrLabel")], "Angelo API key · Grok API")
        self.assertEqual((item[Const("kSecAttrDescription")], item[Const("kSecAttrComment")]), (SERVICE, COMMENT))
        self.assertEqual(self.keychain.read("a@p"), KEY)
        self.assertTrue(self.keychain.delete("a@p"))
        self.assertFalse(self.keychain.delete("a@p"))
        self.assertIsNone(self.keychain.read("a@p"))

    def test_brand_service_is_product_level(self):
        self.assertEqual(SERVICE, "Angelo API key")

    def test_replacing_updates_in_place_and_reads_back(self):
        self.assertIs(self.keychain.write("a@p", KEY, "label"), True)
        self.assertIs(self.keychain.write("a@p", OTHER, "label"), True)
        self.assertEqual([c[0] for c in self.framework.calls], ["add", "copy", "add", "update", "copy"])
        self.assertEqual(self.keychain.read("a@p"), OTHER)

    def test_rebuilt_app_saves_another_builds_item_but_reports_it_unreadable(self):
        self.keychain.write("a@p", KEY, "label")
        self.framework.build = "build-2"
        self.assertIs(self.keychain.write("a@p", OTHER, "label"), False)
        self.framework.build = "build-1"
        self.assertEqual(self.keychain.read("a@p"), OTHER)

    def test_another_builds_item_needs_approval_without_any_prompt(self):
        self.keychain.write("a@p", KEY, "label")
        self.framework.build = "build-2"
        for call in (lambda: self.keychain.read("a@p"), lambda: self.keychain.delete("a@p")):
            with self.assertRaises(KeychainError) as caught:
                call()
            self.assertEqual(caught.exception.kind, "approval")
        self.assertTrue(all(interaction is False for _, interaction, _ in self.framework.calls))
        self.assertTrue(self.keychain.erase("a@p"))
        self.framework.build = "build-1"
        self.assertEqual(self.keychain.read("a@p"), "", "erased: no key material remains")
        self.assertFalse(self.keychain.erase("missing@p"))

    def test_explicit_load_can_approve_or_be_denied(self):
        self.keychain.write("a@p", KEY, "label")
        self.framework.build, self.framework.approve = "build-2", False
        with self.assertRaises(KeychainError) as caught:
            self.keychain.read("a@p", interactive=True)
        self.assertEqual(caught.exception.kind, "denied")
        self.framework.approve = True
        self.assertEqual(self.keychain.read("a@p", interactive=True), KEY)
        self.assertEqual(self.keychain.read("a@p"), KEY, "approved builds read without prompts")

    def test_read_back_mismatch_is_a_failure_without_echo(self):
        original = self.framework.copy_data
        self.framework.copy_data = lambda query: (original(query)[0], b"different")
        with self.assertRaises(KeychainError) as caught:
            self.keychain.write("a@p", KEY, "label")
        self.assertEqual(caught.exception.kind, "failed")
        self.assertNotIn(KEY, str(caught.exception))

    def test_automatic_calls_disallow_prompts_and_restore_the_process_switch(self):
        self.keychain.write("a@p", KEY, "label")
        self.keychain.read("a@p")
        self.keychain.delete("a@p")
        self.assertTrue(all(interaction is False for _, interaction, _ in self.framework.calls))
        self.assertTrue(self.framework.interaction)
        self.keychain.read("a@p", interactive=True)
        self.assertTrue(self.framework.calls[-1][1])
        self.framework.interaction = False  # A caller's own setting is restored too.
        self.keychain.read("a@p", interactive=True)
        self.assertFalse(self.framework.interaction)

    def test_switch_is_restored_when_the_framework_raises(self):
        self.framework.copy_data = lambda query: (_ for _ in ()).throw(OSError("boom"))
        with self.assertRaises(OSError):
            self.keychain.read("a@p")
        self.assertTrue(self.framework.interaction)

    def test_secret_only_travels_as_value_data(self):
        self.keychain.write("a@p", KEY, "label")
        self.keychain.write("a@p", OTHER, "label")
        for _, _, dictionaries in self.framework.calls:
            for dictionary in dictionaries:
                self.assertFalse(any(KEY in str(v) or OTHER in str(v) for v in dictionary.values()))

    def test_errors_are_classified_without_the_secret(self):
        for status, automatic, explicit in ((-25293, "approval", "denied"), (-25244, "approval", "denied"),
                                            (-25308, "approval", "approval"), (-128, "denied", "denied")):
            self.assertEqual((KeychainError(status).kind, KeychainError(status, True).kind), (automatic, explicit))
        self.framework.add_status = -25294
        with self.assertRaises(KeychainError) as caught:
            self.keychain.write("b@p", KEY, "label")
        self.assertEqual(caught.exception.kind, "unavailable")
        self.assertNotIn(KEY, str(caught.exception) + repr(caught.exception))
        self.assertEqual(KeychainError(-1).kind, "failed")
        self.assertIn("OSStatus -1", str(KeychainError(-1)))

    def test_undecodable_data_never_echoes_bytes(self):
        self.framework.items[(SERVICE, "a@p")] = {DATA: b"\xff\xfe" + KEY.encode(), "owner": "build-1"}
        with self.assertRaises(KeychainError) as caught:
            self.keychain.read("a@p")
        self.assertNotIn(KEY, str(caught.exception))


class IdentityTests(unittest.TestCase):
    def test_keychain_error_is_a_key_store_error_with_the_same_text(self):
        error = KeychainError(-25308)
        self.assertIsInstance(error, KeyStoreError)
        self.assertEqual((error.kind, error.status, error.code), ("approval", -25308, "OSStatus -25308"))
        self.assertEqual(str(error), "Keychain needs your approval, or the keychain is locked (OSStatus -25308)")

    def test_profiles_are_path_scoped(self):
        self.assertEqual(profile_id("/tmp/accounts-a"), profile_id("/tmp/accounts-a/"))
        self.assertEqual(profile_id("/tmp/x/../accounts-a"), profile_id("/tmp/accounts-a"))
        self.assertNotEqual(profile_id("/tmp/accounts-a"), profile_id("/tmp/accounts-b"))
        self.assertRegex(profile_id("/tmp/accounts-a"), r"^[0-9a-f]{16}$")
        identity = str(uuid.uuid4())
        self.assertEqual(account_name(identity.upper(), "p1"), identity + "@p1")
        with self.assertRaises(ValueError):
            account_name("not-a-uuid", "p1")


class FakeBackend:
    def __init__(self):
        self.items, self.calls, self.errors = {}, [], {}
        self.gate, self.foreign = None, set()  # foreign: items another build owns

    def _wait(self):
        if self.gate is not None:
            self.gate.wait(5)

    def read(self, account, interactive=False):
        self.calls.append(("read", account, interactive))
        self._wait()
        if account in self.errors:
            raise KeychainError(self.errors[account], interactive)
        return self.items.get(account)

    def write(self, account, secret, label, interactive=False):
        self.calls.append(("write", account, interactive))
        self._wait()
        self.items[account] = secret
        return account not in self.foreign

    def delete(self, account, interactive=False):
        self.calls.append(("delete", account, interactive))
        self._wait()
        if account in self.foreign:
            raise KeychainError(-25244)
        return self.items.pop(account, None) is not None

    def erase(self, account, interactive=False):
        self.calls.append(("erase", account, interactive))
        if account not in self.items:
            return False
        self.items[account] = ""
        return True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def settle(vault, limit=5.0):
    changes, end = [], time.monotonic() + limit
    while time.monotonic() < end:
        changes += vault.pump()
        if not vault._pending:
            return changes
        time.sleep(0.005)
    raise AssertionError("Keychain work did not finish")


def wait_for(condition, limit=5.0):
    end = time.monotonic() + limit
    while not condition():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        time.sleep(0.005)


class SavedKeysTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.clock = FakeBackend(), Clock()
        self.identity = str(uuid.uuid4())
        self.vault = self.open("profile-a")

    def open(self, profile):
        vault = SavedKeys(self.backend, profile, clock=self.clock)
        self.addCleanup(vault.close)
        return vault

    def test_saved_key_survives_a_new_session_only_in_its_profile(self):
        self.vault.save(self.identity, KEY, "label")
        self.assertEqual(settle(self.vault), [(self.identity, "save", "saved", None)])
        self.assertIs(self.vault.stored[self.identity], True)
        restarted = self.open("profile-a")
        restarted.load(self.identity)
        self.assertEqual(settle(restarted), [(self.identity, "load", "loaded", KEY)])
        isolated = self.open("profile-b")
        isolated.load(self.identity)
        self.assertEqual(settle(isolated), [(self.identity, "load", "absent", None)])
        self.assertIs(isolated.stored[self.identity], False)

    def test_forget_deletes_and_only_explicit_loads_may_prompt(self):
        self.vault.save(self.identity, KEY, "label")
        self.vault.load(self.identity)
        self.vault.load(self.identity, interactive=True)
        self.vault.forget(self.identity)
        settle(self.vault)
        self.assertEqual([interactive for *_, interactive in self.backend.calls], [False, False, True, False])
        self.assertEqual(self.vault.state[self.identity], ("forget", "forgotten"))
        self.assertEqual(self.backend.items, {})

    def test_rebuilt_app_outcomes_are_explicit(self):
        account = account_name(self.identity, "profile-a")
        self.backend.foreign.add(account)
        self.vault.save(self.identity, KEY, "label")
        self.assertEqual(settle(self.vault)[0][2], "saved_needs_approval")
        self.assertIsNone(self.vault.stored[self.identity])
        self.vault.forget(self.identity)
        self.assertEqual(settle(self.vault)[0][2], "erased")
        self.assertEqual(self.backend.items[account], "", "the key material is gone")
        self.assertIs(self.vault.stored[self.identity], False)
        restarted = self.open("profile-a")
        restarted.load(self.identity)
        self.assertEqual(settle(restarted)[0][2], "absent", "an erased item is not a key")

    def test_failures_report_an_actionable_kind_and_unknown_storage(self):
        self.backend.errors[account_name(self.identity, "profile-a")] = -25308
        self.vault.load(self.identity)
        self.assertEqual(settle(self.vault), [(self.identity, "load", "approval", None)])
        self.assertIsNone(self.vault.stored[self.identity])
        self.backend.errors[account_name(self.identity, "profile-a")] = -25293
        self.vault.load(self.identity, interactive=True)
        self.assertEqual(settle(self.vault)[0][2], "denied")

    def test_a_prompt_that_never_returns_times_out_and_its_late_result_is_ignored(self):
        self.backend.items[account_name(self.identity, "profile-a")] = KEY
        self.backend.gate = threading.Event()
        self.vault.load(self.identity, interactive=True)
        wait_for(lambda: self.backend.calls)
        self.assertEqual(self.vault.pump(), [])
        self.assertEqual(self.vault.state[self.identity], ("load", "waiting"))
        self.clock.now = self.vault.interactive_timeout + 1
        self.assertEqual(self.vault.pump(), [(self.identity, "load", "timeout", None)])
        self.backend.gate.set()
        time.sleep(0.05)
        self.assertEqual(self.vault.pump(), [])
        self.assertEqual(self.vault.state[self.identity], ("load", "timeout"))

    def test_expired_saves_are_skipped_but_removals_still_run(self):
        second = str(uuid.uuid4())
        self.backend.items[account_name(second, "profile-a")] = OTHER
        self.backend.gate = threading.Event()
        self.vault.load(self.identity, interactive=True)  # Holds the single worker.
        wait_for(lambda: self.backend.calls)
        self.vault.save(second, KEY, "label")
        self.vault.forget(second)
        self.clock.now = 1000.0
        self.assertEqual({c[2] for c in self.vault.pump()}, {"timeout"})
        self.backend.gate.set()
        wait_for(lambda: account_name(second, "profile-a") not in self.backend.items)
        self.assertNotIn("write", [call[0] for call in self.backend.calls])
        wait_for(lambda: not self.vault._results.empty())
        self.assertEqual(self.vault.pump(), [(second, "forget", "forgotten_late", None)])

    def test_a_newer_action_supersedes_an_older_result(self):
        self.backend.items[account_name(self.identity, "profile-a")] = OTHER
        self.backend.gate = threading.Event()
        self.vault.load(self.identity)
        self.vault.save(self.identity, KEY, "label")
        self.backend.gate.set()
        self.assertEqual(settle(self.vault), [(self.identity, "save", "saved", None)])
        self.assertEqual(self.backend.items[account_name(self.identity, "profile-a")], KEY)

    def test_secrets_are_not_retained_or_shown(self):
        operation = self.vault.save(self.identity, KEY, "label")
        self.assertNotIn(KEY, repr(operation))
        settle(self.vault)
        self.assertEqual(operation.secret, "")
        self.assertNotIn(KEY, repr(self.vault.state) + repr(self.vault.stored))

    def test_closed_storage_rejects_work(self):
        self.vault.close()
        with self.assertRaises(RuntimeError):
            self.vault.save(self.identity, KEY, "label")
        self.vault._thread.join(2)
        self.assertFalse(self.vault._thread.is_alive())

    def test_a_backend_without_erase_reports_a_locked_removal(self):
        account = account_name(self.identity, "profile-a")
        backend = type("NoEraseBackend", (FakeBackend,), {"erase": None})()  # As on Windows and Linux.
        backend.foreign.add(account)
        backend.items[account] = KEY
        vault = SavedKeys(backend, "profile-a", clock=self.clock)
        self.addCleanup(vault.close)
        vault.forget(self.identity)
        self.assertEqual(settle(vault), [(self.identity, "forget", "approval", None)])
        self.assertEqual(backend.items[account], KEY, "nothing was overwritten")
        self.assertIsNone(vault.stored[self.identity])


class ProbingBackend(FakeBackend):
    def __init__(self, error=None):
        super().__init__()
        self.error, self.probed = error, 0

    def probe(self):
        self.probed += 1
        if self.error is not None:
            raise self.error


class ProbeTests(unittest.TestCase):
    def open(self, backend):
        vault = SavedKeys(backend, "profile-a", clock=Clock())
        self.addCleanup(vault.close)
        return vault

    def test_unusable_store_answers_unavailable_without_calling_it(self):
        backend = ProbingBackend(KeyStoreError("unavailable", "No keyring service is running", "dbus 2"))
        vault = self.open(backend)
        identity = str(uuid.uuid4())
        vault.load(identity)
        vault.save(identity, KEY, "label")
        vault.forget(identity)
        settle(vault)
        self.assertEqual(backend.calls, [], "no read, write or delete after a failed probe")
        self.assertEqual(vault.unavailable, "No keyring service is running")
        self.assertEqual(vault.state[identity], ("forget", "unavailable"))
        self.assertEqual(backend.probed, 1)

    def test_only_a_store_wide_unavailable_probe_changes_the_mode(self):
        for error in (None, KeyStoreError("approval", "locked"), KeyStoreError("timeout", "slow"), OSError("boom")):
            with self.subTest(error=error):
                backend = ProbingBackend(error)
                vault = self.open(backend)
                identity = str(uuid.uuid4())
                backend.items[account_name(identity, "profile-a")] = KEY
                vault.load(identity)
                self.assertEqual(settle(vault), [(identity, "load", "loaded", KEY)])
                self.assertEqual(vault.unavailable, "")


if __name__ == "__main__":
    unittest.main()
