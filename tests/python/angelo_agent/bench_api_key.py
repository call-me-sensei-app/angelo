# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A gate's pay-per-token API key for the Windows bench user, from GCP Secret Manager. Test-only.

An API-key account of the plan (``final_runs_plan.API_KEY_ACCOUNTS``; Gemini 4 Carbon's ``gemini``) has no sign-in. The
trials run as ``angelobench``, which windows_final_runs.ps1 recreates at every request, so a key saved in its
Credential Manager never outlives one, and a key another Windows user pasted is never readable by it. The maintainer
stores the key once in Secret Manager (maintainer ruling 2026-11-13, "Secret Manager"); at each request whose selection
bills the account, as ``angelobench``:

* ``load``: the account's connections folder gets its one API-key connection (created when absent; a folder holding
  anything else is refused), the secret is read with the VM's own service account (the metadata server's token, the
  Secret Manager REST API), and the key is saved in that connection's Credential Manager item, the item Angelo's AI
  Providers saves (``saved_keys.account_name``, ``key_store``), so a trial's Angelo loads it as a person's saved key.
  The item's shape (service ``key_store.SERVICE``, account ``<connection>@<profile>``, the key's UTF-8 bytes) is a
  stable contract between this tree, which writes it, and the candidate's Angelo, which reads it.
* ``unload``: that item is deleted (the session's end, and the next request's start, in case an end never ran).
* ``check``: the secret is readable and well formed (the kit's readiness check); nothing is saved.

The key lives only in this process's memory: never printed, logged, written to a file, put on a command line or in
an environment variable. Every failure fails closed (exit 2) with a message naming the secret and project and what to
do, never the key: the VM's scopes without ``cloud-platform`` (the only scope Secret Manager accepts), the API turned
off in the project, the service account without ``roles/secretmanager.secretAccessor`` on that one secret, a missing
secret, a disabled or destroyed latest version, and a value that is not one key. ``angelobench`` is a machine account
no person uses, which is why this opens the OS key store from automation (``key_store.store_policy`` keeps background
runs out of a person's store). Any process on the VM can ask the metadata server for the same token, so only the VM
that runs the account's gate is given the ``cloud-platform`` scope (doc/angelo/final_runs.rst).
"""

import argparse
import base64
import http.client
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import final_runs_plan as plan  # noqa: E402

METADATA = "http://metadata.google.internal/computeMetadata/v1/"
ACCESS = "https://secretmanager.googleapis.com/v1/projects/%s/secrets/%s/versions/latest:access"
SECRET_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)
# Secret Manager's naming rule within the bench's own prefix: a key pasted where the NAME belongs never matches it, and
# a name that does not match is never echoed (it may be that key).
SECRET_NAME = re.compile(r"angelo-bench-[A-Za-z0-9_-]{1,242}")
TIMEOUT_S = 20
ERROR_BODY_LIMIT = 65536
# The metadata server is reached directly, never through a proxy an environment variable names (its token reply is
# plain HTTP), as Google's client libraries do.
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
REQUEST_ERRORS = (urllib.error.URLError, OSError, ValueError, http.client.HTTPException)


class Refusal(Exception):
    """Why the key was not loaded. Its message never holds the key."""


class NotOneKey(ValueError):
    """The secret's value is not one key (``well_formed``); its message describes the shape, never the value."""


def secret_name(account):
    return "angelo-bench-%s-key" % account


def label(account):
    return "%s (bench)" % account.capitalize()


def _read(url, headers, opener):
    with opener(urllib.request.Request(url, headers=headers), timeout=TIMEOUT_S) as response:
        return response.read()


def _reason(error):
    """``(status, reasons)`` of a Google API error body; never its message, which may quote the request."""
    try:
        body = json.loads(error.read(ERROR_BODY_LIMIT) or b"{}")
        body = body.get("error") if isinstance(body, dict) else None
    except (ValueError, OSError, AttributeError, http.client.HTTPException):
        return "", set()
    if not isinstance(body, dict):   # An OAuth-style {"error": "invalid_token"}, or no body.
        return "", set()
    details = body.get("details") if isinstance(body.get("details"), list) else []
    return str(body.get("status") or ""), {str(d.get("reason")) for d in details if isinstance(d, dict) and d.get("reason")}


def _refused(error, secret, project):
    status, reasons = _reason(error)
    if "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in reasons:
        why = ("the VM's access scopes lack cloud-platform (set while it is stopped: gcloud compute instances "
               "set-service-account ... --scopes=...,cloud-platform)")
    elif "SERVICE_DISABLED" in reasons:
        why = "the Secret Manager API is turned off in the project (gcloud services enable secretmanager.googleapis.com)"
    elif error.code == 403:
        why = ("the VM's service account may not read it: the maintainer's per-secret binding "
               "(roles/secretmanager.secretAccessor on this secret) is missing")
    elif error.code == 404:
        why = "it does not exist, or has no version: the maintainer creates it or adds a version"
    elif error.code == 400 and status == "FAILED_PRECONDITION":
        why = "its latest version is disabled or destroyed: enable it or add a version"
    else:
        why = "Secret Manager answered HTTP %d %s" % (error.code, status)
    return Refusal("Secret %s in project %s: %s" % (secret, project, why.strip()))


def well_formed(key):
    """Refuse a value that is not one key: whitespace or control characters inside, anything outside printable ASCII
    (a byte-order mark included), surrounding quotes, or a ``NAME=value`` line. The value is never echoed."""
    if not key or any(not 0x21 <= ord(c) <= 0x7e for c in key):
        raise NotOneKey("it is empty, or holds spaces, control or non-ASCII characters (a byte-order mark?)")
    if len(key) > 1 and key[0] == key[-1] and key[0] in "\"'`":
        raise NotOneKey("it is wrapped in quotes")
    if re.match(r"[A-Za-z_][A-Za-z0-9_]*=[^=]", key):   # Base64 padding (``...==``) is no NAME=value.
        raise NotOneKey("it has a NAME=value shape, not the key alone")
    return key


def fetch_secret(secret, opener=urllib.request.urlopen, metadata=DIRECT):
    """The latest version of ``secret`` in this VM's project, read with the VM's service account. In memory only."""
    if not SECRET_NAME.fullmatch(secret or ""):
        raise Refusal("the secret name does not match angelo-bench-[A-Za-z0-9_-] (not echoed: it may be a key)")
    flavor = {"Metadata-Flavor": "Google"}
    try:
        project = _read(METADATA + "project/project-id", flavor, metadata).decode("utf-8").strip()
        scopes = _read(METADATA + "instance/service-accounts/default/scopes", flavor, metadata).decode("utf-8").split()
        token = json.loads(_read(METADATA + "instance/service-accounts/default/token", flavor, metadata))["access_token"]
    except (KeyError, TypeError, *REQUEST_ERRORS) as error:
        raise Refusal("No GCP metadata server or service account token (%s): this runs only on the gate VM"
                      % type(error).__name__) from None
    if not any(scope in scopes for scope in SECRET_SCOPES):
        raise Refusal("Secret %s in project %s: the VM's access scopes lack cloud-platform, the only scope Secret "
                      "Manager accepts (set while it is stopped: gcloud compute instances set-service-account ... "
                      "--scopes=...,cloud-platform)" % (secret, project))
    try:
        reply = json.loads(_read(ACCESS % (project, secret), {"Authorization": "Bearer " + token}, opener))
    except urllib.error.HTTPError as error:
        raise _refused(error, secret, project) from None
    except REQUEST_ERRORS as error:
        raise Refusal("Secret %s in project %s could not be read (%s)" % (secret, project, type(error).__name__)) from None
    try:
        return well_formed(base64.b64decode(reply["payload"]["data"]).decode("utf-8").strip())
    except NotOneKey as error:
        raise Refusal("Secret %s in project %s: %s" % (secret, project, error)) from None
    except (KeyError, TypeError, ValueError):
        raise Refusal("Secret %s in project %s: an unreadable payload" % (secret, project)) from None


def connection(folder, account):
    """The id of the account's one API-key connection in ``folder``, created when the folder holds none."""
    from angelo_agent.connections import ConnectionStore
    from angelo_agent.provider_registry import SPECS
    mode = plan.API_KEY_ACCOUNTS[account]
    spec = SPECS[mode]
    try:
        store = ConnectionStore(Path(folder))
        if not store.records:   # ``add`` saves the record.
            store.add(spec["provider"], spec["method"], name=label(account), group=spec["group"])
    except (OSError, ValueError) as error:
        raise Refusal("The connections folder %s could not be prepared (%s: %s)" % (folder, type(error).__name__,
                                                                                    error)) from None
    found = [identity for identity, record in store.records.items() if record.get("mode") == mode]
    if len(found) != 1 or len(store.records) != 1:
        raise Refusal("%s must hold only one %s connection (it holds %d connections)" % (folder, mode,
                                                                                     len(store.records)))
    return found[0]


def _account(folder, identity):
    from angelo_agent.saved_keys import account_name, profile_id
    try:
        return account_name(identity, profile_id(folder))   # The profile resolves the folder, as the app does.
    except (OSError, ValueError, RuntimeError) as error:
        raise Refusal("The key-store item of %s could not be named (%s)" % (folder, type(error).__name__)) from None


def open_backend():
    """This machine's key store. Automation's own account, never a person's (module docstring)."""
    from angelo_agent import key_store
    backend, reason = key_store.open_store(background=False)   # ANGELO_DISABLE_KEYCHAIN still turns it off.
    if backend is None:
        raise Refusal("The OS key store is unavailable here: %s" % reason)
    return backend


def _call(operation, *args):
    from angelo_agent.key_store import STORE_CALLS
    try:
        with STORE_CALLS:
            return operation(*args)
    except Exception as error:   # A store failure says its kind, never its arguments.
        raise Refusal("The OS key store refused (%s %s)" % (type(error).__name__, getattr(error, "kind", ""))) from None


def load(folder, account, secret, *, fetch=None, backend=None):
    """Save the secret's key in the folder's connection's key-store item; returns the connection id. ``fetch``
    defaults to this module's ``fetch_secret`` as it is when called."""
    identity = connection(folder, account)
    backend = backend or open_backend()
    if _call(backend.write, _account(folder, identity), (fetch or fetch_secret)(secret), label(account)) is not True:
        raise Refusal("The key was saved but could not be read back in this session")
    return identity


def unload(folder, account, *, backend=None):
    """Delete the folder's connection's key-store item. True when one was there."""
    from angelo_agent.connections import ConnectionStore
    mode = plan.API_KEY_ACCOUNTS[account]
    try:
        found = [i for i, r in ConnectionStore(Path(folder)).records.items() if r.get("mode") == mode]
    except (OSError, ValueError) as error:
        raise Refusal("The connections folder %s could not be read (%s)" % (folder, type(error).__name__)) from None
    if not found:
        return False
    backend = backend or open_backend()
    return bool(_call(backend.delete, _account(folder, found[0])))


def main(argv=None):
    parser = argparse.ArgumentParser(description="A gate's API key for the bench user, from Secret Manager.")
    parser.add_argument("action", choices=("load", "unload", "check"))
    parser.add_argument("--account", default="gemini", choices=sorted(plan.API_KEY_ACCOUNTS))
    parser.add_argument("--connections-dir", default="")
    parser.add_argument("--secret", default="")
    args = parser.parse_args(argv)
    secret = args.secret or secret_name(args.account)
    try:
        if args.action == "check":
            fetch_secret(secret)
            print("secret %s: readable, one well-formed key" % secret)
        elif not args.connections_dir:
            raise Refusal("--connections-dir is required")
        elif args.action == "load":
            print("key loaded for connection %s" % load(args.connections_dir, args.account, secret))
        else:
            print("key item removed" if unload(args.connections_dir, args.account) else "no key item to remove")
    except Refusal as refusal:
        print("bench_api_key %s: %s" % (args.action, refusal), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
