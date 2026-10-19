# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Windows Credential Manager backend, on any host OS through a fake advapi32.

The fake receives exactly the ctypes arguments the real binding passes
(CREDENTIALW structures, ``byref`` pointers, wide-string targets), so these
tests run the actual marshalling code: item identity, UTF-8 blobs, size
limits, buffer zeroing, CredFree and Windows error mapping. Every test runs
twice: calling the fake directly, and through real C function pointers with
the binding's declared signatures (``fake_c_libraries.CAbiLibrary``), so
ctypes' own argument conversion is exercised too. They cannot prove behavior
on real Windows; see "Manual checks on Windows" in doc/angelo/ai_providers.rst.
"""

import ctypes
import sys
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent.credential_manager_windows import (_FUNCTIONS, CRED_MAX_CREDENTIAL_BLOB_SIZE,
                                                     CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC, CREDENTIALW,
                                                     LPBYTE, CredentialApi, CredentialManagerStore)
from angelo_agent.key_store import COMMENT, SERVICE, KeyStoreError
from angelo_agent.saved_keys import SavedKeys, account_name
from fake_c_libraries import CAbiLibrary, CFunction, address, store, struct
from test_native_saved_keys import KEY, OTHER, settle

ACCOUNT = "%s@0123456789abcdef" % uuid.UUID(int=1)
LABEL = "Angelo API key · Grok API"


class FakeAdvapi32:
    """Credential Manager semantics on a dict, called with advapi32's ctypes arguments.

    Targets compare case-insensitively, as on Windows. ``error`` makes every
    call fail with that Windows error code.
    """

    def __init__(self):
        self.vault, self.calls, self.error, self.last = {}, [], None, 0
        self.written, self.outstanding, self.freed_blobs = [], {}, []
        for name in ("CredWriteW", "CredReadW", "CredDeleteW", "CredFree"):
            setattr(self, name, CFunction(getattr(self, "_" + name)))

    def last_error(self):
        return self.last

    def _fail(self, code):
        self.last = code
        return 0

    def _CredWriteW(self, credential_ref, flags):
        credential = struct(credential_ref)
        self.calls.append(("write", credential.TargetName, flags))
        if self.error:
            return self._fail(self.error)
        size = credential.CredentialBlobSize
        self.written.append(credential)  # Kept alive to check that the caller zeroed its buffer.
        self.vault[credential.TargetName.lower()] = {
            "type": credential.Type, "target": credential.TargetName, "user": credential.UserName,
            "comment": credential.Comment, "persist": credential.Persist, "flags": credential.Flags,
            "blob": ctypes.string_at(credential.CredentialBlob, size) if size else b""}
        return 1

    def _CredReadW(self, target, kind, flags, pointer_ref):
        self.calls.append(("read", target, flags))
        if self.error:
            return self._fail(self.error)
        entry = self.vault.get(target.lower())
        if entry is None or entry["type"] != kind:
            return self._fail(1168)
        data = entry["blob"]
        blob = (ctypes.c_ubyte * max(len(data), 1)).from_buffer_copy(data.ljust(1, b"\0"))
        credential = CREDENTIALW(Type=kind, TargetName=entry["target"], UserName=entry["user"],
                                 Comment=entry["comment"], Persist=entry["persist"], CredentialBlobSize=len(data),
                                 CredentialBlob=ctypes.cast(blob, LPBYTE))
        self.outstanding[ctypes.addressof(credential)] = (credential, blob, len(data))
        store(pointer_ref, credential)
        return 1

    def _CredDeleteW(self, target, kind, flags):
        self.calls.append(("delete", target, flags))
        if self.error:
            return self._fail(self.error)
        if self.vault.pop(target.lower(), None) is None:
            return self._fail(1168)
        return 1

    def _CredFree(self, pointer):
        _credential, blob, size = self.outstanding.pop(address(pointer))
        self.freed_blobs.append(bytes(blob)[:size])  # What the memory held when it was freed.


def api(test, advapi, c_abi):
    library = advapi
    if c_abi:
        library = CAbiLibrary(advapi, _FUNCTIONS)
        test.addCleanup(lambda: test.assertEqual(library.errors, [], "no fake call raised"))
    return CredentialApi(library, advapi.last_error)


class CredentialManagerTests(unittest.TestCase):
    C_ABI = False

    def setUp(self):
        self.advapi = FakeAdvapi32()
        self.store = CredentialManagerStore(api(self, self.advapi, self.C_ABI))

    def test_round_trip_and_item_identity(self):
        self.assertIsNone(self.store.read(ACCOUNT))
        self.assertIs(self.store.write(ACCOUNT, KEY, LABEL), True)
        item = self.advapi.vault[("%s:%s" % (SERVICE, ACCOUNT)).lower()]
        self.assertEqual(item["target"], "Angelo API key:" + ACCOUNT)
        self.assertEqual((item["type"], item["persist"], item["user"], item["flags"]),
                         (CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE, ACCOUNT, 0))
        self.assertEqual(item["comment"], LABEL + ". " + COMMENT)
        self.assertEqual(item["blob"], KEY.encode("utf-8"))
        self.assertEqual(self.store.read(ACCOUNT), KEY)
        self.assertIs(self.store.delete(ACCOUNT), True)
        self.assertIs(self.store.delete(ACCOUNT), False)
        self.assertIsNone(self.store.read(ACCOUNT))
        self.assertEqual(self.advapi.outstanding, {}, "every successful read is freed")

    def test_replacing_overwrites_in_place(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        self.store.write(ACCOUNT.upper(), OTHER, LABEL)  # Windows targets are case-insensitive.
        self.assertEqual(len(self.advapi.vault), 1)
        self.assertEqual(self.store.read(ACCOUNT), OTHER)

    def test_profiles_and_connections_never_share_an_item(self):
        identity = str(uuid.uuid4())
        self.store.write(account_name(identity, "profile-a"), KEY, LABEL)
        self.assertIsNone(self.store.read(account_name(identity, "profile-b")))
        self.assertIsNone(self.store.read(account_name(str(uuid.uuid4()), "profile-a")))

    def test_unicode_secret_is_stored_as_utf8(self):
        secret = "tést-kéy-ключ-🔑"
        self.store.write(ACCOUNT, secret, LABEL)
        self.assertEqual(self.advapi.vault[("Angelo API key:" + ACCOUNT).lower()]["blob"], secret.encode("utf-8"))
        self.assertEqual(self.store.read(ACCOUNT), secret)

    def test_empty_and_oversized_secrets_are_refused_before_any_call(self):
        with self.assertRaises(KeyStoreError) as caught:
            self.store.write(ACCOUNT, "", LABEL)
        self.assertEqual(caught.exception.kind, "failed")
        biggest = "k" * CRED_MAX_CREDENTIAL_BLOB_SIZE
        self.assertIs(self.store.write(ACCOUNT, biggest, LABEL), True)
        calls = len(self.advapi.calls)
        for secret in ("k" * (CRED_MAX_CREDENTIAL_BLOB_SIZE + 1), "é" * (CRED_MAX_CREDENTIAL_BLOB_SIZE // 2 + 1)):
            with self.assertRaises(KeyStoreError) as caught:
                self.store.write(ACCOUNT, secret, LABEL)
            self.assertEqual(caught.exception.kind, "too_long")
            self.assertNotIn(secret, str(caught.exception))
        self.assertEqual(len(self.advapi.calls), calls, "nothing reached Credential Manager")
        self.assertEqual(self.store.read(ACCOUNT), biggest)

    def test_an_empty_blob_reads_as_empty(self):
        self.advapi.vault[("Angelo API key:" + ACCOUNT).lower()] = {
            "type": CRED_TYPE_GENERIC, "target": "Angelo API key:" + ACCOUNT, "user": ACCOUNT, "comment": "",
            "persist": CRED_PERSIST_LOCAL_MACHINE, "flags": 0, "blob": b""}
        self.assertEqual(self.store.read(ACCOUNT), "")

    def test_secret_buffers_are_zeroed(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        if not self.C_ABI:  # Through the C ABI the caller's buffer is gone once the call returns.
            written = self.advapi.written[0]
            self.assertEqual(ctypes.string_at(written.CredentialBlob, written.CredentialBlobSize),
                             b"\0" * len(KEY), "the write buffer is zeroed after CredWriteW")
        self.assertTrue(self.advapi.freed_blobs, "the write's read-back was freed")
        self.assertTrue(all(blob == b"\0" * len(blob) for blob in self.advapi.freed_blobs),
                        "each returned CredentialBlob is zeroed before CredFree")

    def test_long_labels_fit_the_comment_limit(self):
        self.store.write(ACCOUNT, KEY, "Angelo API key · " + "x" * 400)
        self.assertEqual(len(self.advapi.vault[("Angelo API key:" + ACCOUNT).lower()]["comment"]), 255)

    def test_interactive_changes_nothing_because_nothing_prompts(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        self.advapi.calls.clear()
        self.store.read(ACCOUNT, interactive=True)
        self.store.read(ACCOUNT)
        self.assertEqual(self.advapi.calls[0], self.advapi.calls[1])

    def test_errors_are_classified_without_the_secret(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        for code, kind in ((1312, "unavailable"), (50, "unavailable"), (5, "denied"), (87, "failed"),
                           (1004, "failed"), (2202, "failed"), (31, "failed")):
            self.advapi.error = code
            for call in (lambda: self.store.read(ACCOUNT), lambda: self.store.write(ACCOUNT, KEY, LABEL),
                         lambda: self.store.delete(ACCOUNT)):
                with self.subTest(code=code), self.assertRaises(KeyStoreError) as caught:
                    call()
                self.assertEqual(caught.exception.kind, kind)
                self.assertIn("Windows error %d" % code, str(caught.exception))
                self.assertNotIn(KEY, str(caught.exception) + repr(caught.exception))

    def test_read_back_mismatch_and_undecodable_data_never_echo(self):
        original = self.advapi._CredReadW

        def corrupted(target, kind, flags, pointer_ref):
            entry = self.advapi.vault.get(target.lower())
            if entry is not None:
                entry["blob"] = b"\xff\xfe" + KEY.encode()
            return original(target, kind, flags, pointer_ref)
        self.advapi.CredReadW.function = corrupted
        for call in (lambda: self.store.write(ACCOUNT, KEY, LABEL), lambda: self.store.read(ACCOUNT)):
            with self.assertRaises(KeyStoreError) as caught:
                call()
            self.assertEqual(caught.exception.kind, "failed")
            self.assertNotIn(KEY, str(caught.exception) + repr(caught.exception))

    def test_probe_accepts_a_working_vault_and_rejects_a_session_without_one(self):
        self.store.probe()
        self.assertEqual(self.advapi.calls[-1][:2], ("read", "Angelo API key:availability-check"))
        self.advapi.error = 1312
        with self.assertRaises(KeyStoreError) as caught:
            self.store.probe()
        self.assertEqual(caught.exception.kind, "unavailable")
        self.assertIn("sign-in session", caught.exception.message)

    @unittest.skipIf(sys.platform == "win32", "advapi32 exists on Windows")
    def test_missing_library_fails_at_construction(self):
        with self.assertRaises((AttributeError, OSError)):
            CredentialApi()


class CredentialManagerCAbiTests(CredentialManagerTests):
    """The same checks with every call converted by ctypes as for the real advapi32."""

    C_ABI = True


class SavedKeysOnCredentialManagerTests(unittest.TestCase):
    """The shared worker driving this backend through the C ABI, as the app does."""

    def setUp(self):
        self.advapi = FakeAdvapi32()
        self.store = CredentialManagerStore(api(self, self.advapi, c_abi=True))
        self.identity = str(uuid.uuid4())

    def open(self):
        vault = SavedKeys(self.store, "profile-a")
        self.addCleanup(vault.close)
        return vault

    def test_save_restart_load_and_forget(self):
        vault = self.open()
        vault.save(self.identity, KEY, LABEL)
        self.assertEqual(settle(vault), [(self.identity, "save", "saved", None)])
        restarted = self.open()
        restarted.load(self.identity)
        self.assertEqual(settle(restarted), [(self.identity, "load", "loaded", KEY)])
        restarted.forget(self.identity)
        self.assertEqual(settle(restarted), [(self.identity, "forget", "forgotten", None)])
        self.assertEqual(self.advapi.vault, {})

    def test_a_session_without_a_vault_becomes_memory_only(self):
        self.advapi.error = 1312
        vault = self.open()
        vault.load(self.identity)
        self.assertEqual(settle(vault), [(self.identity, "load", "unavailable", None)])
        self.assertIn("sign-in session", vault.unavailable)
        self.assertEqual([call[1] for call in self.advapi.calls], ["Angelo API key:availability-check"])

    def test_oversized_key_is_reported_as_too_long(self):
        vault = self.open()
        vault.save(self.identity, "k" * (CRED_MAX_CREDENTIAL_BLOB_SIZE + 1), LABEL)
        self.assertEqual(settle(vault), [(self.identity, "save", "too_long", None)])


if __name__ == "__main__":
    unittest.main()
