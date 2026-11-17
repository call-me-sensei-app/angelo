# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in checks through the REAL Windows Credential Manager (LIVE class).

The Windows counterpart of ``test_blender_keychain_live``. A host-Python
driver: it writes, reads, replaces and deletes generic credentials holding fake
keys, only under unique targets whose service starts with ``TEST_PREFIX``
(never Angelo's own ``key_store.SERVICE``), so no existing item is read,
listed or printed. Items are deleted in ``finally`` and the receipt says
whether any was left.

1. Host round trip, as Angelo stores an organization endpoint's key: the item
   of one person's key slot (``endpoint_connections.key_identity``) holds the
   JSON value ``endpoint_ui.set_key`` saves, reads back to the same origin and
   key, and is unreadable through another person's slot, another organization's
   and another profile's (a shared workstation: one OS user, several Angelo
   accounts). Replace, delete, a second delete, and a value over Windows' 2560
   bytes refused without leaving an item. Then the ``SavedKeys`` worker's save,
   load and forget of the same slot.
2. The app: the built binary (``--background``) reads the item host Python
   wrote, through the app's own Python and advapi32 binding, compares it with a
   digest it is given (the value is never passed or printed), and deletes it.

Requires Windows, a signed-in desktop session (Credential Manager is
unavailable over SSH or in a service's session) and
``ANGELO_QA_LIVE_KEYCHAIN=1``::

    set ANGELO_QA_LIVE_KEYCHAIN=1
    python tests/python/angelo_agent/run_native_suites.py --binary <Angelo.exe> --allow-live \\
        --include test_blender_credential_manager_live
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import argparse
import hashlib
import json
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if "--child" not in sys.argv:   # Inside the app, its own bundled add-on and binding answer; host Python takes the tree's.
    sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.key_store import KeyStoreError, store_policy  # noqa: E402
from angelo_agent.saved_keys import SavedKeys, account_name, profile_id  # noqa: E402

TEST_PREFIX = "Angelo QA test-only item "
CALL_LIMIT = 10.0   # seconds; Credential Manager never prompts, so a call returns in milliseconds
CONSOLE = "https://console.test-only.invalid"
ENDPOINT = uuid.uuid4().hex
ORIGIN = "https://llm.test-only.invalid:443"


def fake_key():
    return "test-key-not-real-" + uuid.uuid4().hex[:12]


def stored(key, origin=ORIGIN):
    """The value ``endpoint_ui.set_key`` keeps for a member's key."""
    return json.dumps({"origin": origin, "key": key})


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def bounded(timings, name, function, *args):
    """One Credential Manager call on a worker, so a hang fails the check instead of the suite."""
    outcome = {}

    def run():
        try:
            outcome["value"] = function(*args)
        except BaseException as error:  # noqa: BLE001 - re-raised below without its arguments.
            outcome["error"] = error
    started = time.perf_counter()
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(CALL_LIMIT)
    timings[name] = round((time.perf_counter() - started) * 1000, 3)
    if worker.is_alive():
        raise TimeoutError("%s did not return within %.0fs" % (name, CALL_LIMIT))
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def slot(org, user):
    from angelo_agent.endpoint_connections import key_identity
    return key_identity(CONSOLE, org, user, ENDPOINT)


def host_round_trip(report, created, scratch):
    from angelo_agent.credential_manager_windows import CredentialManagerStore
    from angelo_agent.endpoint_ui import stored_value
    timings, checks = report["timings_ms"], report["checks"]
    store = CredentialManagerStore(service=TEST_PREFIX + str(uuid.uuid4()))
    profile = profile_id(scratch / "accounts-a")
    mine = account_name(slot("org-1", "user-1"), profile)
    others = {"other_person": account_name(slot("org-1", "user-2"), profile),
              "other_organization": account_name(slot("org-2", "user-1"), profile),
              "other_profile": account_name(slot("org-1", "user-1"), profile_id(scratch / "accounts-b"))}
    created.extend((store, a) for a in (mine, *others.values()))
    first, second = fake_key(), fake_key()
    checks["app_policy_off_for_automation"] = (not store_policy({"ANGELO_DISABLE_KEYCHAIN": "1"}, "win32", False)[0]
                                               and not store_policy({}, "win32", True)[0])
    bounded(timings, "probe", store.probe)   # Raises ``unavailable`` outside a desktop session.
    checks["vault_available"] = True
    checks["absent_item_reads_none"] = bounded(timings, "read_absent", store.read, mine) is None
    bounded(timings, "create", store.write, mine, stored(first), "Angelo organization endpoint key QA")
    checks["endpoint_value_reads_back"] = stored_value(bounded(timings, "read_created", store.read, mine)) == (
        ORIGIN, first)
    for name, account in others.items():
        checks[name + "_cannot_read"] = bounded(timings, "read_" + name, store.read, account) is None
    bounded(timings, "replace", store.write, mine, stored(second), "Angelo organization endpoint key QA")
    checks["replacement_reads_back"] = stored_value(bounded(timings, "read_replaced", store.read, mine)) == (
        ORIGIN, second)
    checks["delete_removes_item"] = bounded(timings, "delete", store.delete, mine) is True
    checks["deleted_item_is_gone"] = bounded(timings, "read_deleted", store.read, mine) is None
    checks["second_delete_reports_absent"] = bounded(timings, "delete_absent", store.delete, mine) is False
    try:
        bounded(timings, "oversized", store.write, mine, stored("k" * 2600), "Angelo organization endpoint key QA")
        refused = False
    except KeyStoreError:
        refused = True
    checks["oversized_value_refused_and_not_stored"] = refused and bounded(
        timings, "read_oversized", store.read, mine) is None
    vault = SavedKeys(store, profile)
    try:
        identity = slot("org-1", "user-1")
        for kind, call in (("save", lambda: vault.save(identity, stored(first), "Angelo organization endpoint key QA")),
                           ("load", lambda: vault.load(identity)), ("forget", lambda: vault.forget(identity))):
            call()
            end, changes = time.monotonic() + CALL_LIMIT, []
            while not changes and time.monotonic() < end:
                changes = vault.pump()
                time.sleep(0.005)
            checks["worker_" + kind] = bool(changes) and changes[0][2] in {"saved", "loaded", "forgotten"}
    finally:
        vault.close()


def in_app(report, created, binary, scratch):
    """Host Python writes the item; the app binary reads it with its own Python and binding, then deletes it."""
    from angelo_agent.credential_manager_windows import CredentialManagerStore
    service = TEST_PREFIX + str(uuid.uuid4())
    store = CredentialManagerStore(service=service)
    account = account_name(slot("org-1", "user-1"), profile_id(scratch / "accounts-app"))
    created.append((store, account))
    value = stored(fake_key())
    bounded(report["timings_ms"], "app_host_create", store.write, account, value, "Angelo organization endpoint key QA")
    child_out = scratch / "in-app.json"
    command = [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
               "--python", str(Path(__file__).resolve()), "--", "--child", service, account, digest(value),
               str(child_out)]
    try:
        code = subprocess.run(command, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=120).returncode
    except subprocess.TimeoutExpired:
        code = "timeout"
    child = json.loads(child_out.read_text()) if child_out.is_file() else {"error": "no child report"}
    report["in_app"] = dict(child, exit_code=code)
    checks = report["checks"]
    checks["app_reads_the_endpoint_value"] = code == 0 and child.get("read") == "match"
    checks["app_deletes_it"] = child.get("delete") is True
    checks["app_delete_seen_by_host"] = bounded(report["timings_ms"], "app_host_read_after", store.read,
                                                account) is None


def child_main(service, account, expected, out):
    """Inside the app binary: read, compare by digest, delete; record kinds, never values."""
    result = {}
    try:
        from angelo_agent.credential_manager_windows import CredentialManagerStore
        store = CredentialManagerStore(service=service)
        value = bounded({}, "read", store.read, account)
        result["read"] = "absent" if value is None else "match" if digest(value) == expected else "different"
        result["delete"] = bounded({}, "delete", store.delete, account)
    except Exception as error:  # noqa: BLE001 - its kind only.
        result["error"] = type(error).__name__ + ": " + getattr(error, "kind", "")
    Path(out).write_text(json.dumps(result, indent=2))
    os._exit(0 if "error" not in result else 1)


def host_main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"success": False, "checks": {}, "timings_ms": {},
              "fixture": "Throwaway test-only generic credentials with fake keys; deleted afterwards"}
    created = []
    if sys.platform != "win32" or os.environ.get("ANGELO_QA_LIVE_KEYCHAIN") != "1":
        report["error"] = "Requires Windows and ANGELO_QA_LIVE_KEYCHAIN=1 (writes test-only Credential Manager items)"
    else:
        scratch = Path(tempfile.mkdtemp(prefix="credential-live-", dir=out))
        try:
            host_round_trip(report, created, scratch)
            in_app(report, created, Path(args.binary), scratch)
            report["success"] = all(report["checks"].values())
        except Exception as error:  # noqa: BLE001 - reported in the receipt (a KeyStoreError carries no secret).
            report.update(error=type(error).__name__ + ": " + str(error), traceback=traceback.format_exc())
        finally:
            leftovers = []
            for store, account in created:
                try:
                    bounded(report["timings_ms"], "cleanup", store.delete, account)
                    if bounded(report["timings_ms"], "cleanup_read", store.read, account) is not None:
                        leftovers.append(account)
                except Exception:  # noqa: BLE001 - counted as a leftover.
                    leftovers.append(account)
            report["checks"]["cleanup_left_nothing"] = not leftovers
            report["success"] = report["success"] and not leftovers
    (out / "credential-manager-live.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_CREDENTIAL_MANAGER_LIVE", json.dumps({"success": report["success"], "checks": report["checks"]}),
          flush=True)
    return 0 if report["success"] else 1


if "--child" in sys.argv:
    position = sys.argv.index("--child")
    child_main(*sys.argv[position + 1:position + 5])
elif __name__ == "__main__":
    sys.exit(host_main())
