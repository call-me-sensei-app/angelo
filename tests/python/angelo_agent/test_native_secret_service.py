# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Linux Secret Service backend, on any host OS through a fake libsecret.

The fake stands in for libsecret-1, GLib, GObject and GIO and receives exactly
the ctypes arguments the real binding passes (the SecretSchema structure,
GHashTable handles built from g_strdup'd strings, ``byref`` GError and length
pointers, GList nodes), over an in-memory Secret Service with collections,
lock state and unlock prompts. These tests run the actual marshalling code,
the prompt policy and reference counting (every handle, string, list, value
and GError must be released). Every test runs twice: calling the fake
directly, and through real C function pointers with the binding's declared
signatures (``fake_c_libraries.CAbiLibrary``), so ctypes' own argument
conversion is exercised too. They cannot prove behavior on a real desktop; see
"Manual checks on Linux" in doc/angelo/ai_providers.rst.
"""

import ctypes
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent.key_store import KeyStoreError
from angelo_agent.saved_keys import SavedKeys, account_name
from angelo_agent.secret_service_linux import (_FUNCTIONS, KDE_NO_COLLECTION, KDE_NO_SERVICE, NO_COLLECTION,
                                               NO_SERVICE, SCHEMA, SECRET_ITEM_CREATE_REPLACE, SECRET_SEARCH_ALL,
                                               SECRET_SEARCH_LOAD_SECRETS, SECRET_SEARCH_UNLOCK,
                                               SECRET_SERVICE_OPEN_SESSION, GError, GList, LibSecret,
                                               SecretServiceStore, kde_session)
from fake_c_libraries import CAbiLibrary, CFunction, set_value, store, struct
from test_native_saved_keys import KEY, OTHER, settle, wait_for

ACCOUNT = "%s@0123456789abcdef" % uuid.UUID(int=1)
LABEL = "Angelo API key · Gemini Images"
SECRET_Q, DBUS_Q, IO_Q = 101, 102, 103
STR_HASH, STR_EQUAL, G_FREE = 0x10, 0x20, 0x30


class FakeLibSecret:
    """libsecret and the GLib calls it needs, as ctypes calls them, over an in-memory Secret Service.

    ``running`` False: no Secret Service on the bus. ``prompt``: the person's
    answer to an unlock prompt ("approve", "dismiss") or an Event the prompt
    waits on. ``fail[name] = (domain_quark, code)`` makes that call return a
    GError whose message contains a secret, which the binding must never read.
    """

    def __init__(self):
        self.running, self.prompt, self.prompts, self.fail = True, "approve", 0, {}
        self.collections, self.aliases = {"login": {"locked": False}}, {"default": "login"}
        self.items, self.calls, self.on_create = [], [], None
        self.objects, self.strings, self.tables, self.lists, self.values, self.errors = {}, {}, {}, {}, {}, {}
        self.value_inputs, self.errors_freed, self._next = [], 0, 1000
        self.g_str_hash, self.g_str_equal, self.g_free = STR_HASH, STR_EQUAL, G_FREE
        for name in ("secret_error_get_quark", "secret_service_get_sync", "secret_collection_for_alias_sync",
                     "secret_collection_get_locked", "secret_service_search_sync", "secret_item_get_locked",
                     "secret_item_get_secret", "secret_item_create_sync", "secret_item_delete_sync",
                     "secret_value_new", "secret_value_get", "secret_value_unref", "g_strdup",
                     "g_hash_table_new_full", "g_hash_table_insert", "g_hash_table_unref", "g_list_free",
                     "g_error_free", "g_quark_to_string", "g_object_unref", "g_dbus_error_quark", "g_io_error_quark"):
            setattr(self, name, CFunction(getattr(self, "_" + name)))

    def load(self, name):
        return self

    # Bookkeeping -----------------------------------------------------------

    def _handle(self, kind, payload):
        self._next += 8
        self.objects[self._next] = [kind, payload, 1]
        return self._next

    def _object(self, handle, kind):
        entry = self.objects.get(handle)
        assert entry is not None and entry[2] > 0 and entry[0] == kind, (handle, kind)
        return entry[1]

    def _error(self, error_ref, name):
        if name in self.fail:
            domain, code = self.fail[name]
            error = GError(domain, code, ("message mentioning " + KEY).encode())
            self.errors[ctypes.addressof(error)] = error
            store(error_ref, error)
            return True
        return False

    def leaks(self):
        return {"objects": [h for h, entry in self.objects.items() if entry[2] > 0], "strings": list(self.strings),
                "tables": list(self.tables), "lists": list(self.lists), "values": list(self.values),
                "errors": list(self.errors)}

    def _unlock(self, collection):
        self.prompts += 1
        if isinstance(self.prompt, threading.Event):
            self.prompt.wait(10)
            return
        if self.prompt == "approve":
            self.collections[collection]["locked"] = False

    # GLib, GObject, GIO ----------------------------------------------------

    def _g_strdup(self, value):
        buffer = ctypes.create_string_buffer(value)
        self.strings[ctypes.addressof(buffer)] = buffer
        return ctypes.addressof(buffer)

    def _g_hash_table_new_full(self, hash_function, equal_function, key_destroy, value_destroy):
        assert (hash_function, equal_function, key_destroy, value_destroy) == (STR_HASH, STR_EQUAL, G_FREE, G_FREE)
        self._next += 8
        self.tables[self._next] = {}
        return self._next

    def _g_hash_table_insert(self, table, key, value):
        self.tables[table][self.strings[key].value.decode()] = (key, value, self.strings[value].value.decode())
        return 1

    def _g_hash_table_unref(self, table):
        for key_address, value_address, _value in self.tables.pop(table).values():
            self.strings.pop(key_address)  # The table's g_free destroy functions.
            self.strings.pop(value_address)

    def _attributes(self, table):
        return {key: value for key, (_key, _address, value) in self.tables[table].items()}

    def _g_list_free(self, head):
        self.lists.pop(head)

    def _g_error_free(self, error):
        self.errors.pop(ctypes.addressof(error.contents))
        self.errors_freed += 1

    def _g_quark_to_string(self, quark):
        return b"g-unknown-quark"

    def _g_object_unref(self, handle):
        entry = self.objects.get(handle)
        assert entry is not None and entry[2] > 0, "unref of a released handle"
        entry[2] -= 1

    def _g_dbus_error_quark(self):
        return DBUS_Q

    def _g_io_error_quark(self):
        return IO_Q

    # libsecret -------------------------------------------------------------

    def _secret_error_get_quark(self):
        return SECRET_Q

    def _secret_service_get_sync(self, flags, cancellable, error_ref):
        self.calls.append(("service", flags))
        if not self.running:
            self.fail.setdefault("secret_service_get_sync", (DBUS_Q, 2))
        if self._error(error_ref, "secret_service_get_sync"):
            return None
        return self._handle("service", None)

    def _secret_collection_for_alias_sync(self, service, alias, flags, cancellable, error_ref):
        self._object(service, "service")
        self.calls.append(("alias", alias))
        if self._error(error_ref, "secret_collection_for_alias_sync"):
            return None
        name = self.aliases.get(alias.decode())
        return self._handle("collection", name) if name else None

    def _secret_collection_get_locked(self, collection):
        return int(self.collections[self._object(collection, "collection")]["locked"])

    def _secret_service_search_sync(self, service, schema_ref, table, flags, cancellable, error_ref):
        self._object(service, "service")
        schema = struct(schema_ref)
        self.calls.append(("search", flags, schema.name, dict(self._attributes(table))))
        if self._error(error_ref, "secret_service_search_sync"):
            return None
        wanted = dict(self._attributes(table), **{"xdg:schema": schema.name.decode()})
        matches = [item for item in self.items if all(item["attributes"].get(k) == v for k, v in wanted.items())]
        matches.sort(key=lambda item: self.collections[item["collection"]]["locked"])  # Unlocked first.
        if flags & SECRET_SEARCH_UNLOCK:
            locked = {item["collection"] for item in matches if self.collections[item["collection"]]["locked"]}
            for collection in locked:
                self._unlock(collection)
        if not flags & SECRET_SEARCH_ALL:
            matches = matches[:1]
        if not matches:
            return None
        handles = [self._handle("item", {"item": item, "loaded": bool(flags & SECRET_SEARCH_LOAD_SECRETS)
                                         and not self.collections[item["collection"]]["locked"]})
                   for item in matches]
        nodes = [GList(handle, None, None) for handle in handles]
        for node, following in zip(nodes, nodes[1:]):
            node.next = ctypes.pointer(following)
        self.lists[ctypes.addressof(nodes[0])] = nodes
        return ctypes.addressof(nodes[0])

    def _secret_item_get_locked(self, item):
        return int(self.collections[self._object(item, "item")["item"]["collection"]]["locked"])

    def _secret_item_get_secret(self, item):
        entry = self._object(item, "item")
        if not entry["loaded"] or self.collections[entry["item"]["collection"]]["locked"]:
            return None
        return self._value(entry["item"]["secret"])

    def _value(self, data):
        self._next += 8
        self.values[self._next] = (ctypes.create_string_buffer(data, max(len(data), 1)), len(data))
        return self._next

    def _secret_value_new(self, buffer, length, content_type):
        assert content_type == b"text/plain"
        self.value_inputs.append((buffer, length))  # Kept to check that the caller zeroes it.
        return self._value(ctypes.string_at(buffer, length))

    def _secret_value_get(self, value, length_ref):
        buffer, length = self.values[value]
        set_value(length_ref, length)
        return ctypes.addressof(buffer)

    def _secret_value_unref(self, value):
        self.values.pop(value)

    def _secret_item_create_sync(self, collection, schema_ref, table, label, value, flags, cancellable, error_ref):
        name = self._object(collection, "collection")
        schema = struct(schema_ref)
        self.calls.append(("create", flags, label.decode()))
        if self.collections[name]["locked"]:
            self.fail.setdefault("secret_item_create_sync", (SECRET_Q, 2))
        if self._error(error_ref, "secret_item_create_sync"):
            self.fail.pop("secret_item_create_sync", None)
            return None
        attributes = dict(self._attributes(table), **{"xdg:schema": schema.name.decode()})
        if flags & SECRET_ITEM_CREATE_REPLACE:
            self.items = [i for i in self.items if not (i["collection"] == name and i["attributes"] == attributes)]
        buffer, length = self.values[value]
        item = {"collection": name, "attributes": attributes, "label": label.decode(), "secret": buffer.raw[:length]}
        self.items.append(item)
        if self.on_create:
            self.on_create()
        return self._handle("item", {"item": item, "loaded": False})

    def _secret_item_delete_sync(self, item, cancellable, error_ref):
        entry = self._object(item, "item")
        self.calls.append(("delete",))
        if self._error(error_ref, "secret_item_delete_sync"):
            return 0
        self.items.remove(entry["item"])
        return 1


def library(test, fake, c_abi):
    if not c_abi:
        return LibSecret(fake.load)
    signatures = [signature for functions in _FUNCTIONS.values() for signature in functions]
    c_library = CAbiLibrary(fake, signatures, symbols=("g_str_hash", "g_str_equal", "g_free"))
    test.addCleanup(lambda: test.assertEqual(c_library.errors, [], "no fake call raised"))
    return LibSecret(lambda name: c_library)


class SecretServiceTests(unittest.TestCase):
    C_ABI = False

    def setUp(self):
        self.fake = FakeLibSecret()
        self.store = SecretServiceStore(library(self, self.fake, self.C_ABI))
        self.addCleanup(self.assert_released)

    def assert_released(self):
        self.assertEqual(self.fake.leaks(), {"objects": [], "strings": [], "tables": [], "lists": [], "values": [],
                                             "errors": []})

    def calls(self, kind):
        return [call for call in self.fake.calls if call[0] == kind]

    def test_round_trip_and_item_identity(self):
        self.assertIsNone(self.store.read(ACCOUNT))
        self.assertIs(self.store.write(ACCOUNT, KEY, LABEL), True)
        [item] = self.fake.items
        self.assertEqual(item["collection"], "login", "the default alias's collection")
        self.assertEqual(item["attributes"], {"service": "Angelo API key", "account": ACCOUNT,
                                              "xdg:schema": "org.angelo.ApiKey"})
        self.assertEqual((item["label"], item["secret"]), (LABEL, KEY.encode()))
        self.assertEqual(self.store.read(ACCOUNT), KEY)
        self.assertIs(self.store.delete(ACCOUNT), True)
        self.assertIs(self.store.delete(ACCOUNT), False)
        self.assertIsNone(self.store.read(ACCOUNT))
        self.assertTrue(all(flags == SECRET_SERVICE_OPEN_SESSION for _, flags in self.calls("service")))
        self.assertEqual({call[1] for call in self.calls("create")}, {SECRET_ITEM_CREATE_REPLACE})

    def test_schema_structure_matches_libsecret(self):
        schema = self.store.library.schema
        self.assertEqual((schema.name, schema.flags), (SCHEMA.encode(), 0))
        self.assertEqual([(a.name, a.type) for a in schema.attributes[:3]], [(b"service", 0), (b"account", 0),
                                                                              (None, 0)])

    def test_replacing_keeps_one_item(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        self.store.write(ACCOUNT, OTHER, LABEL)
        self.assertEqual(len(self.fake.items), 1)
        self.assertEqual(self.store.read(ACCOUNT), OTHER)

    def test_profiles_and_connections_never_share_an_item(self):
        identity = str(uuid.uuid4())
        self.store.write(account_name(identity, "profile-a"), KEY, LABEL)
        self.assertIsNone(self.store.read(account_name(identity, "profile-b")))
        self.assertIsNone(self.store.read(account_name(str(uuid.uuid4()), "profile-a")))

    def test_unicode_and_long_secrets_round_trip(self):
        for secret in ("tést-kéy-ключ-🔑", "k" * 4096):
            self.store.write(ACCOUNT, secret, LABEL)
            self.assertEqual(self.store.read(ACCOUNT), secret)

    def test_empty_secret_is_refused_before_any_call(self):
        with self.assertRaises(KeyStoreError) as caught:
            self.store.write(ACCOUNT, "", LABEL)
        self.assertEqual(caught.exception.kind, "failed")
        self.assertEqual(self.fake.calls, [])

    def test_secret_buffer_is_zeroed_after_libsecret_copies_it(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        buffer, length = self.fake.value_inputs[0]
        self.assertEqual(length, len(KEY))
        if not self.C_ABI:  # Through the C ABI the caller's buffer is gone once the call returns.
            self.assertEqual(buffer.raw[:length], b"\0" * length)
        self.assertEqual(self.fake.items[0]["secret"], KEY.encode())

    def test_automatic_calls_never_unlock_a_locked_keyring(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        self.fake.collections["login"]["locked"] = True
        for call in (lambda: self.store.read(ACCOUNT), lambda: self.store.write(ACCOUNT, OTHER, LABEL),
                     lambda: self.store.delete(ACCOUNT)):
            with self.assertRaises(KeyStoreError) as caught:
                call()
            self.assertEqual(caught.exception.kind, "approval")
        self.assertEqual(self.fake.prompts, 0)
        self.assertFalse(any(call[1] & SECRET_SEARCH_UNLOCK for call in self.calls("search")))
        self.assertEqual(len(self.calls("create")), 1, "a locked keyring is refused before creating")
        self.assertEqual(self.fake.items[0]["secret"], KEY.encode(), "nothing changed")

    def test_explicit_load_may_unlock_and_reports_a_dismissed_prompt(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        self.fake.collections["login"]["locked"] = True
        self.fake.prompt = "dismiss"
        with self.assertRaises(KeyStoreError) as caught:
            self.store.read(ACCOUNT, interactive=True)
        self.assertEqual(caught.exception.kind, "denied")
        self.fake.prompt = "approve"
        self.assertEqual(self.store.read(ACCOUNT, interactive=True), KEY)
        self.assertEqual(self.fake.prompts, 2)
        self.assertEqual(self.calls("search")[-1][1], SECRET_SEARCH_LOAD_SECRETS | SECRET_SEARCH_UNLOCK)
        self.assertEqual(self.store.read(ACCOUNT), KEY, "unlocked keyrings read without prompts")

    def test_keyring_locking_before_the_read_back_is_saved_but_unconfirmed(self):
        self.fake.on_create = lambda: self.fake.collections["login"].update(locked=True)
        self.assertIs(self.store.write(ACCOUNT, KEY, LABEL), False)
        self.assertEqual(self.fake.prompts, 0)

    def test_missing_service_or_default_keyring_is_unavailable(self):
        self.fake.aliases.clear()
        for call in (self.store.probe, lambda: self.store.write(ACCOUNT, KEY, LABEL)):
            with self.assertRaises(KeyStoreError) as caught:
                call()
            self.assertEqual(caught.exception.kind, "unavailable")
            self.assertIn("no default collection", caught.exception.message)
        self.fake.running = False
        for call in (self.store.probe, lambda: self.store.read(ACCOUNT), lambda: self.store.delete(ACCOUNT)):
            with self.assertRaises(KeyStoreError) as caught:
                call()
            self.assertEqual((caught.exception.kind, caught.exception.code), ("unavailable", "dbus 2"))
            self.assertIn("No keyring service", caught.exception.message)

    def test_probe_accepts_a_locked_keyring(self):
        self.fake.collections["login"]["locked"] = True
        self.store.probe()
        self.assertEqual(self.fake.prompts, 0)

    def test_glib_errors_are_classified_freed_and_never_echoed(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        cases = (((DBUS_Q, 4), "timeout"), ((DBUS_Q, 20), "timeout"), ((DBUS_Q, 9), "unavailable"),
                 ((DBUS_Q, 2), "unavailable"), ((IO_Q, 19), "denied"), ((SECRET_Q, 2), "approval"),
                 ((SECRET_Q, 3), "unavailable"), ((SECRET_Q, 1), "failed"), ((999, 7), "failed"))
        for error, kind in cases:
            with self.subTest(error=error):
                self.fake.fail["secret_service_search_sync"] = error
                with self.assertRaises(KeyStoreError) as caught:
                    self.store.read(ACCOUNT)
                self.assertEqual(caught.exception.kind, kind)
                self.assertNotIn(KEY, str(caught.exception) + repr(caught.exception))
        self.assertEqual(str(caught.exception), "Keyring error (g-unknown-quark 7)")
        self.assertEqual(self.fake.errors_freed, len(cases))
        self.fake.fail = {"secret_service_search_sync": (SECRET_Q, 2)}
        with self.assertRaises(KeyStoreError) as caught:
            self.store.read(ACCOUNT, interactive=True)
        self.assertEqual(caught.exception.kind, "denied", "a lock that survives an explicit unlock is a refusal")

    def test_a_failed_delete_is_reported_and_released(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        self.fake.fail["secret_item_delete_sync"] = (DBUS_Q, 0)
        with self.assertRaises(KeyStoreError) as caught:
            self.store.delete(ACCOUNT)
        self.assertEqual(caught.exception.kind, "failed")

    def test_undecodable_stored_bytes_never_echo(self):
        self.store.write(ACCOUNT, KEY, LABEL)
        self.fake.items[0]["secret"] = b"\xff\xfe" + KEY.encode()
        with self.assertRaises(KeyStoreError) as caught:
            self.store.read(ACCOUNT)
        self.assertEqual(caught.exception.kind, "failed")
        self.assertNotIn(KEY, str(caught.exception) + repr(caught.exception))

    def test_missing_or_incomplete_library_fails_at_construction(self):
        def missing(name):
            raise OSError(name)
        with self.assertRaises(OSError):
            LibSecret(missing)
        incomplete = FakeLibSecret()
        del incomplete.secret_item_create_sync
        with self.assertRaises(AttributeError):
            LibSecret(incomplete.load)


class SecretServiceCAbiTests(SecretServiceTests):
    """The same checks with every call converted by ctypes as for the real libsecret."""

    C_ABI = True


class SavedKeysOnSecretServiceTests(unittest.TestCase):
    """The shared worker driving this backend through the C ABI, as the app does."""

    def setUp(self):
        self.fake = FakeLibSecret()
        self.store = SecretServiceStore(library(self, self.fake, c_abi=True))
        self.identity = str(uuid.uuid4())

    def open(self, clock=time.monotonic):
        return self.open_with(self.store, clock)

    def open_with(self, store, clock=time.monotonic):
        vault = SavedKeys(store, "profile-a", clock=clock)
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
        self.assertEqual(self.fake.items, [])

    def test_no_secret_service_switches_to_memory_only_without_touching_items(self):
        self.fake.running = False
        vault = self.open()
        vault.load(self.identity)
        self.assertEqual(settle(vault), [(self.identity, "load", "unavailable", None)])
        self.assertIn("No keyring service", vault.unavailable)
        self.assertEqual([call[0] for call in self.fake.calls], ["service"], "only the probe ran")

    def test_kde_session_without_a_secret_service_says_how_to_turn_it_on(self):
        bus = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
        cases = ((dict(bus, XDG_CURRENT_DESKTOP="KDE"), KDE_NO_SERVICE, KDE_NO_COLLECTION),
                 (dict(bus, KDE_FULL_SESSION="true"), KDE_NO_SERVICE, KDE_NO_COLLECTION),
                 (dict(bus, XDG_CURRENT_DESKTOP="ubuntu:GNOME"), NO_SERVICE, NO_COLLECTION))
        for environ, no_service, no_collection in cases:
            with self.subTest(environ=environ):
                store = SecretServiceStore.open(environ, load=self.fake.load)
                self.fake.running, self.fake.fail = False, {}
                vault = self.open_with(store)
                vault.load(self.identity)
                self.assertEqual(settle(vault), [(self.identity, "load", "unavailable", None)])
                self.assertEqual(vault.unavailable, no_service)
                self.fake.running, self.fake.fail = True, {"secret_service_search_sync": (DBUS_Q, 3)}
                with self.assertRaises(KeyStoreError) as caught:  # The provider left the bus mid-session.
                    store.read(ACCOUNT)
                self.assertEqual(caught.exception.message, no_service)
                self.fake.fail, aliases = {}, dict(self.fake.aliases)
                self.fake.aliases.clear()  # KWallet answers ReadAlias("default") with "/" until a wallet exists.
                with self.assertRaises(KeyStoreError) as caught:
                    store.probe()
                self.assertEqual((caught.exception.kind, caught.exception.message), ("unavailable", no_collection))
                self.fake.aliases.update(aliases)
        self.assertIn("System Settings", KDE_NO_SERVICE)
        self.assertIn("Use KWallet for the Secret Service interface", KDE_NO_SERVICE)

    def test_kde_session_detection(self):
        for environ in ({"XDG_CURRENT_DESKTOP": "KDE"}, {"XDG_CURRENT_DESKTOP": "X-Custom:KDE"},
                        {"KDE_FULL_SESSION": "true"}):
            self.assertTrue(kde_session(environ), environ)
        for environ in ({}, {"XDG_CURRENT_DESKTOP": "GNOME"}, {"XDG_CURRENT_DESKTOP": "KDEish"},
                        {"XDG_CURRENT_DESKTOP": "kde"}, {"KDE_FULL_SESSION": "false"}, {"DESKTOP_SESSION": "plasma"}):
            self.assertFalse(kde_session(environ), environ)

    def test_locked_keyring_at_startup_offers_the_explicit_load(self):
        vault = self.open()
        vault.save(self.identity, KEY, LABEL)
        settle(vault)
        self.fake.collections["login"]["locked"] = True
        vault.load(self.identity)
        self.assertEqual(settle(vault), [(self.identity, "load", "approval", None)])
        self.assertIsNone(vault.stored[self.identity])
        vault.load(self.identity, interactive=True)
        self.assertEqual(settle(vault), [(self.identity, "load", "loaded", KEY)])
        self.assertEqual(self.fake.prompts, 1)

    def test_an_unanswered_unlock_prompt_times_out_and_its_late_result_is_ignored(self):
        class Clock:
            now = 0.0

            def __call__(self):
                return self.now
        clock = Clock()
        vault = self.open(clock)
        vault.save(self.identity, KEY, LABEL)
        settle(vault)
        self.fake.collections["login"]["locked"] = True
        self.fake.prompt = threading.Event()
        vault.load(self.identity, interactive=True)
        wait_for(lambda: self.fake.prompts == 1)
        self.assertEqual(vault.pump(), [])
        self.assertEqual(vault.state[self.identity], ("load", "waiting"))
        clock.now = vault.interactive_timeout + 1
        self.assertEqual(vault.pump(), [(self.identity, "load", "timeout", None)])
        self.fake.prompt.set()
        wait_for(lambda: not vault._results.empty())
        self.assertEqual(vault.pump(), [])
        self.assertEqual(vault.state[self.identity], ("load", "timeout"))


if __name__ == "__main__":
    unittest.main()
