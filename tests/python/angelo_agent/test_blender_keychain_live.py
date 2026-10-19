# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in checks through the REAL macOS Security framework (LIVE class).

Host-Python driver. It writes, replaces, reads and deletes fake-key generic
passwords in the caller's login Keychain, only under unique service names
starting with ``TEST_PREFIX``; it never queries another service, so no
existing item is read, listed or printed. Every call disallows Keychain
prompts, so an item that would need approval, or a locked keychain, fails at
once instead of waiting; a call still running after ``CALL_LIMIT`` means a
prompt appeared and fails the suite. Items are deleted in ``finally``.

1. Host round trip: the binding, item identity, profile isolation, in-place
   replacement and the ``SavedKeys`` worker, as host Python.
2. Cross identity: host Python creates an item; the ad-hoc signed app binary
   (a different code identity, like a rebuilt development app) then reads,
   replaces and deletes it, prompt-free. This is the rebuild/approval path.

Requires macOS and ``ANGELO_QA_LIVE_KEYCHAIN=1``::

    ANGELO_QA_LIVE_KEYCHAIN=1 python3 tests/python/angelo_agent/run_native_suites.py \\
        --binary <Angelo> --allow-live --include test_blender_keychain_live
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.key_store import store_policy
from angelo_agent.keychain_macos import KeychainError, MacKeychain, SecurityFramework
from angelo_agent.saved_keys import SavedKeys, account_name, profile_id

TEST_PREFIX = "Angelo QA test-only item "
CALL_LIMIT = 10.0  # seconds; a prompt-free call returns in milliseconds


def fake_key():
    return "test-key-not-real-" + uuid.uuid4().hex[:12]


def bounded(timings, name, function, *args):
    """One Keychain call on a worker, so a prompt fails the check instead of hanging the suite."""
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
        raise TimeoutError("%s did not return within %.0fs (a Keychain prompt?)" % (name, CALL_LIMIT))
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("value")


def host_round_trip(report, created, scratch):
    timings, checks = report["timings_ms"], report["checks"]
    service = TEST_PREFIX + str(uuid.uuid4())
    keychain = MacKeychain(SecurityFramework(), service=service)
    initial = keychain.framework.interaction_allowed()
    identity = str(uuid.uuid4())
    account = account_name(identity, profile_id(scratch / "accounts-a"))
    other = account_name(identity, profile_id(scratch / "accounts-b"))
    created.extend((keychain, a) for a in (account, other))
    first, second = fake_key(), fake_key()
    checks["app_policy_off_for_automation"] = (not store_policy({"ANGELO_DISABLE_KEYCHAIN": "1"}, "darwin", False)[0]
                                               and not store_policy({}, "darwin", True)[0])
    checks["absent_item_reads_none"] = bounded(timings, "read_absent", keychain.read, account) is None
    bounded(timings, "create", keychain.write, account, first, "Angelo API key QA")
    checks["created_item_reads_back"] = bounded(timings, "read_created", keychain.read, account) == first
    bounded(timings, "replace", keychain.write, account, second, "Angelo API key QA")
    checks["replacement_reads_back"] = bounded(timings, "read_replaced", keychain.read, account) == second
    checks["other_profile_cannot_read"] = bounded(timings, "read_other_profile", keychain.read, other) is None
    checks["delete_removes_item"] = bounded(timings, "delete", keychain.delete, account) is True
    checks["deleted_item_is_gone"] = bounded(timings, "read_deleted", keychain.read, account) is None
    checks["second_delete_reports_absent"] = bounded(timings, "delete_absent", keychain.delete, account) is False
    checks["process_prompt_switch_restored"] = keychain.framework.interaction_allowed() == initial
    vault = SavedKeys(keychain, profile_id(scratch / "accounts-a"))
    try:
        for kind, call in (("save", lambda: vault.save(identity, first, "Angelo API key QA")),
                           ("load", lambda: vault.load(identity)), ("forget", lambda: vault.forget(identity))):
            call()
            end, changes = time.monotonic() + CALL_LIMIT, []
            while not changes and time.monotonic() < end:
                changes = vault.pump()
                time.sleep(0.005)
            checks["worker_" + kind] = bool(changes) and changes[0][2] in {"saved", "loaded", "forgotten"}
    finally:
        vault.close()


def cross_identity(report, created, binary, scratch):
    """Host Python owns the item; the app binary must handle it without any prompt."""
    service = TEST_PREFIX + str(uuid.uuid4())
    keychain = MacKeychain(SecurityFramework(), service=service)
    account = account_name(str(uuid.uuid4()), profile_id(scratch / "accounts-cross"))
    created.append((keychain, account))
    bounded(report["timings_ms"], "cross_host_create", keychain.write, account, fake_key(), "Angelo API key QA")
    child_out = scratch / "cross-identity.json"
    command = [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
               "--python", str(Path(__file__).resolve()), "--", "--cross", service, account, str(child_out)]
    started = time.perf_counter()
    try:
        code = subprocess.run(command, cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=120).returncode
    except subprocess.TimeoutExpired:
        code = "timeout"
    report["timings_ms"]["cross_child_process"] = round((time.perf_counter() - started) * 1000, 3)
    child = json.loads(child_out.read_text()) if child_out.is_file() else {"error": "no child report"}
    report["cross_identity"] = dict(child, exit_code=code)
    checks = report["checks"]
    checks["cross_identity_never_prompted"] = code == 0 and child.get("prompt_free") is True
    # Another build's item: unreadable without approval, reported as such (never "denied").
    checks["cross_identity_read_needs_approval"] = child.get("read") in {"approval", "readable"}
    checks["cross_identity_save_is_verified_or_flagged"] = (
        child.get("write") == "saved" and child.get("read_after_write") == "readable"
        or child.get("write") == "saved_needs_approval" and child.get("read_after_write") == "approval")
    # Measured on macOS 26.3: after another build overwrote the secret, even the
    # creating identity needs approval to read it, so the erase is evidenced by
    # its OSStatus, not by a read-back (which would need a prompt).
    try:
        host_value = bounded(report["timings_ms"], "cross_host_read_after", keychain.read, account)
        report["cross_identity"]["creator_read_after"] = "absent" if host_value is None else (
            "empty" if host_value == "" else "key")
    except KeychainError as error:
        report["cross_identity"]["creator_read_after"] = error.kind
    checks["cross_identity_removal_removes_key_material"] = (
        child.get("delete") == "deleted" and report["cross_identity"]["creator_read_after"] == "absent"
        or child.get("delete") == "approval" and child.get("erase") is True
        and report["cross_identity"]["creator_read_after"] in {"empty", "approval"})


def child_main(service, account, out):
    """Inside the app binary: every call prompt-free; record kinds and OSStatus codes, never values."""
    result, timings = {"prompt_free": False, "timings_ms": {}, "statuses": []}, {}
    framework = SecurityFramework()
    for name in ("add", "update", "copy_data", "delete"):
        def logged(*args, _call=getattr(framework, name), _name=name):
            value = _call(*args)
            result["statuses"].append([_name, value[0] if isinstance(value, tuple) else value])
            return value
        setattr(framework, name, logged)
    keychain = MacKeychain(framework, service=service)

    def attempt(name, function, *args):
        try:
            value = bounded(timings, name, function, *args)
            return "readable" if isinstance(value, str) else value
        except KeychainError as error:
            return error.kind
    try:
        result["read"] = attempt("cross_read", keychain.read, account)
        written = attempt("cross_write", keychain.write, account, fake_key(), "Angelo API key QA")
        result["write"] = {True: "saved", False: "saved_needs_approval"}.get(written, written)
        result["read_after_write"] = attempt("cross_read_after_write", keychain.read, account)
        deleted = attempt("cross_delete", keychain.delete, account)
        result["delete"] = {True: "deleted", False: "absent"}.get(deleted, deleted)
        if result["delete"] == "approval":
            result["erase"] = attempt("cross_erase", keychain.erase, account)
        result["prompt_free"] = True
    except TimeoutError as error:
        result["error"] = str(error)
    result["timings_ms"] = timings
    Path(out).write_text(json.dumps(result, indent=2))
    os._exit(0 if result["prompt_free"] else 1)  # Never wait on an abandoned, blocked call.


def host_main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"success": False, "checks": {}, "timings_ms": {},
              "fixture": "Throwaway test-only generic passwords with fake keys; deleted afterwards"}
    created = []
    if sys.platform != "darwin" or os.environ.get("ANGELO_QA_LIVE_KEYCHAIN") != "1":
        report["error"] = "Requires macOS and ANGELO_QA_LIVE_KEYCHAIN=1 (writes test-only login-Keychain items)"
    else:
        scratch = Path(tempfile.mkdtemp(prefix="keychain-live-", dir=out))
        try:
            host_round_trip(report, created, scratch)
            cross_identity(report, created, Path(args.binary), scratch)
            report["success"] = all(report["checks"].values())
        except Exception as error:  # noqa: BLE001 - reported in the receipt.
            report.update(error=type(error).__name__ + ": " + str(error), traceback=traceback.format_exc())
        finally:
            leftovers = []
            for keychain, account in created:
                try:
                    bounded(report["timings_ms"], "cleanup", keychain.delete, account)
                    if bounded(report["timings_ms"], "cleanup_read", keychain.read, account) is not None:
                        leftovers.append(account)
                except Exception:  # noqa: BLE001 - counted as a leftover.
                    leftovers.append(account)
            report["checks"]["cleanup_left_nothing"] = not leftovers
            report["success"] = report["success"] and not leftovers
    (out / "keychain-live.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_KEYCHAIN_LIVE", json.dumps({"success": report["success"], "checks": report["checks"]}), flush=True)
    return 0 if report["success"] else 1


if "--cross" in sys.argv:
    position = sys.argv.index("--cross")
    child_main(*sys.argv[position + 1:position + 4])
elif __name__ == "__main__":
    sys.exit(host_main())
