# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""LIVE end-to-end check of the team sign-in against a real Angelo console (not billed).

Runs the app's own modules (``team_account``, ``team_token_store`` with the
real OS store, ``team_lock``, ``team_grants``) outside Blender, with Blender's
Python. It opens the system browser at the console's allow screen; a person
signs in there and clicks Allow. The script never sees a password, never
prints a token, code or key, and calls only the console (no provider, no
gateway model route):

1. sign in (PKCE, loopback, ``iss``), store the refresh token in the OS store;
2. check the access token's header and claims against the console's JWKS
   (``alg``, ``kid``, ``iss``, ``aud``, ``org``, ``seat``, ``scope``; the
   signature itself needs Ed25519, which the standard library lacks);
3. ``providers/available``;
4. a forced refresh (the token rotates; concurrent callers share one refresh);
5. two other processes restore the same stored sign-in at the same moment
   (``team_lock``): both sign in with the shared access token
   (``team_access_share``) without rotating the sign-in, and nothing is revoked;
6. a grant key, stored for the other windows, and adopted by a second account
   without a second issue;
7. Your devices: this computer is listed (``GET /api/me/devices``);
8. the organization's app policy: the token answer carries ``policy`` (all
   five fields), it equals ``GET /api/orgs/{orgId}/app-policy``, and the app
   applies it (``managed_policy.current``, kept by ``team_org_policy``);
9. the end of the sign-in, by ``--end-with``: ``devices`` signs this computer
   out the way Your devices does (``DELETE /api/me/devices/{grant}``), and the
   app must then say it was signed out, for the console's ``error_reason``
   ``signed_out``, and erase its items; ``sign-out`` uses the app's own Sign
   out (revoke; refreshing afterwards is refused);
10. after ``devices``: the next sign-in link carries ``login_hint`` (the ended
    account's email) and ``org_hint`` (``--organization``, or else the
    organization just signed in to); it is checked and cancelled, or with
    ``--look-again SECONDS`` opened so a person can see the organization
    pre-selected (an Allow there is checked against the hint, then signed
    out).

``--organization`` sets the managed ``Organization`` (the app then sends
``org_hint``, so the allow screen pre-selects it, and refuses another).

The OS-store items use a profile derived from a fresh temporary folder, so
they never touch the person's own Angelo sign-in, and are deleted at the end.

    <Blender>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/live_team_sign_in.py \\
        --console https://console.angelo.studio --report <tmp>/team-sign-in.json
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from urllib.parse import parse_qs, urlsplit  # noqa: E402

from angelo_agent import key_store, managed_policy, team_lock, team_token_store  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402
from angelo_agent.team_org_policy import OrganizationPolicy  # noqa: E402
from angelo_agent.team_view import org_policy  # noqa: E402
from angelo_agent.saved_keys import profile_id  # noqa: E402
from angelo_agent.team_account import TeamAccount  # noqa: E402
from angelo_agent.team_endpoints import parse_origin  # noqa: E402
from angelo_agent.team_errors import SignInRequired  # noqa: E402
from angelo_agent.team_grants import decode  # noqa: E402
from angelo_agent.team_http import form_body, http_transport  # noqa: E402
from angelo_agent.team_signin import CLIENT_ID  # noqa: E402

_CHILD = """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from angelo_agent import team_lock, team_token_store
from angelo_agent.team_account import TeamAccount
from angelo_agent.managed_policy import Policy
console, profile, folder, go = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
backend, _ = team_token_store.open_backend({}, sys.platform, False)
access, _ = team_token_store.open_backend({}, sys.platform, False, team_token_store.ACCESS_SERVICE,
                                          team_token_store.ACCESS_COMMENT)
vault = team_token_store.TokenVault(backend, console, profile)
account = TeamAccount(console, vault=vault, access_vault=team_token_store.TokenVault(access, console, profile),
                      process_lock=team_lock.factory(folder), policy=Policy)
import os
while not os.path.exists(go):
    time.sleep(0.005)
account.restore()
deadline = time.monotonic() + 60
while account.view().status == "restoring" and time.monotonic() < deadline:
    time.sleep(0.01)
account.drain()  # As at exit: the rotated token is stored before the process ends.
print(json.dumps({"status": account.view().status, "message": str(account.view().message)}))
"""


def claims_of(token):
    def part(index):
        value = token.split(".")[index]
        return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
    return part(0), part(1)


def wait(predicate, seconds, step=0.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--console", default="https://console.angelo.studio")
    parser.add_argument("--device", default="Angelo live sign-in check")
    parser.add_argument("--report", required=True)
    parser.add_argument("--no-open", action="store_true", help="print the sign-in link instead of opening it")
    parser.add_argument("--organization", default="", help="the managed Organization (UUID): sends org_hint")
    parser.add_argument("--look-again", type=int, default=0, metavar="SECONDS",
                        help="open the next sign-in link (org_hint, login_hint) and wait this long for an Allow")
    parser.add_argument("--end-with", choices=("devices", "sign-out"), default="devices",
                        help="how the sign-in ends: Your devices (the console signs this computer out) or Sign out")
    args = parser.parse_args()
    console = parse_origin(args.console, "--console").url
    report = {"console": console, "steps": {}}
    steps = report["steps"]
    folder = tempfile.mkdtemp(prefix="angelo-team-live-")
    profile = profile_id(folder)
    backend, reason = team_token_store.open_backend(os.environ, sys.platform, False)
    grant_backend, _ = team_token_store.open_backend(os.environ, sys.platform, False, team_token_store.GRANT_SERVICE,
                                                     team_token_store.GRANT_COMMENT)
    access_backend, _ = team_token_store.open_backend(os.environ, sys.platform, False, team_token_store.ACCESS_SERVICE,
                                                      team_token_store.ACCESS_COMMENT)
    if backend is None:
        raise SystemExit("The OS store is unavailable: %s" % reason)
    vault = team_token_store.TokenVault(backend, console, profile)
    grant_vault = team_token_store.TokenVault(grant_backend, console, profile, label="Angelo team grant key")
    access_vault = team_token_store.TokenVault(access_backend, console, profile, label="Angelo team access")
    gone = lambda: vault.read() is None and grant_vault.read() is None and access_vault.read() is None  # noqa: E731
    report["store"] = {"service": backend.service, "grant_service": grant_backend.service,
                       "store": key_store.store_text().name}
    policies = [Policy(organization=args.organization.lower(), keys=frozenset({"Organization"}))
                if args.organization else Policy()]
    account = TeamAccount(console, vault=vault, grant_vault=grant_vault, access_vault=access_vault,
                          process_lock=team_lock.factory(folder), policy=lambda: policies[-1])
    try:
        url = account.begin_sign_in(device=args.device)
        hints = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items() if k in {"org_hint", "login_hint"}}
        report["first_link_hints"] = {"org_hint": hints.get("org_hint", ""), "login_hint": "login_hint" in hints}
        print("Sign in and click Allow in the browser (waiting up to 10 minutes):\n" + url, flush=True)
        if not args.no_open and sys.platform == "darwin":
            subprocess.run(["open", url], check=False)
        wait(lambda: account.view().status != "signing_in", 610, 0.5)
        if not wait(lambda: account.view().signed_in and account.view().providers is not None, 30):
            steps["sign_in"] = {"ok": False, "status": account.view().status, "message": str(account.view().message)}
            return report
        view = account.view()
        steps["sign_in"] = {"ok": True, "org": view.org_name, "seat": view.seat, "scopes": sorted(view.scopes),
                            "stored": view.stored, "email_domain": view.email.rpartition("@")[2]}
        steps["store_holds_live_token"] = {"ok": vault.read() == account._refresh_token}

        access = account.access_token()
        header, claims = claims_of(access)
        _status, _headers, data = http_transport("GET", console + "/.well-known/jwks.json")
        kids = {key.get("kid") for key in json.loads(data).get("keys", [])}
        steps["access_token"] = {
            "ok": header.get("alg") == "EdDSA" and header.get("kid") in kids and claims.get("iss") == console
            and set(claims.get("aud", [])) >= {"angelo-api", "angelo-gateway"} and claims.get("org") == view.org_id,
            "alg": header.get("alg"), "kid_published": header.get("kid") in kids, "iss": claims.get("iss"),
            "aud": claims.get("aud"), "seat": claims.get("seat"), "role": claims.get("role"),
            "scope": claims.get("scope"), "lifetime": claims.get("exp", 0) - claims.get("iat", 0)}

        steps["providers_available"] = {"ok": view.providers_error == "", "providers": list(view.providers or ()),
                                        "error": str(view.providers_error)}

        before = vault.read()
        results = []
        threads = [threading.Thread(target=lambda: results.append(account.access_token(stale=access))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        after = vault.read()
        steps["refresh"] = {"ok": len(set(results)) == 1 and results[0] != access and after != before,
                            "callers": len(results), "distinct_tokens": len(set(results)), "rotated": after != before}

        stored_before = vault.read()
        go = os.path.join(folder, "go")
        children = [subprocess.Popen([sys.executable, "-c", _CHILD, str(ROOT / "scripts/addons_core"), console,
                                      profile, folder, go], stdout=subprocess.PIPE, text=True) for _ in range(2)]
        time.sleep(1.0)
        Path(go).touch()
        outcomes = [json.loads(child.communicate(timeout=120)[0].strip().splitlines()[-1]) for child in children]
        rotated = vault.read() != stored_before
        steps["other_processes"] = {"ok": all(o["status"] == "signed_in" for o in outcomes) and not rotated,
                                    "children": outcomes, "rotated_by_children": rotated}
        account.access_token(stale=results[0] if results else access)  # Adopts the children's newer token.
        steps["other_processes"]["parent_adopted"] = account.view().signed_in

        if not view.seat:  # The console refuses a key without a seat: check the person is told so.
            account._enqueue("grant_key")
            wait(lambda: account.view().grant_error, 30)
            steps["grant_key"] = {"ok": "seat" in str(account.view().grant_error) and not account.grant_key(),
                                  "refused_without_seat": str(account.view().grant_error)}
        elif view.can_generate:
            account._enqueue("grant_key")
            wait(lambda: account.grant_key() or account.view().grant_error, 30)
            key = account.grant_key()
            shared = decode(grant_vault.read() or "")
            steps["grant_key"] = {"ok": bool(key) and shared is not None and shared[0] == key,
                                  "format": key[:4] + "…(%d chars)" % len(key) if key else "",
                                  "hours": round(((account.view().grant_key_expires or 0) - time.time()) / 3600, 1),
                                  "error": str(account.view().grant_error)}
            second = TeamAccount(console, vault=vault, grant_vault=grant_vault, access_vault=access_vault,
                                 process_lock=team_lock.factory(folder))
            second.restore()
            wait(lambda: second.view().signed_in and second.view().providers is not None, 60)
            second._enqueue("grant_key")
            wait(lambda: second.grant_key(), 30)
            steps["grant_key_shared"] = {"ok": bool(key) and second.grant_key() == key}
            second.close()
        else:
            steps["grant_key"] = {"ok": None, "skipped": "no generate scope: the console refuses grant keys"}

        _header, claims = claims_of(account.access_token())
        listed = account.api_get("/api/me/devices").get("data")
        mine = next((d for d in listed or [] if isinstance(d, dict) and d.get("id") == claims.get("gid")), None)
        steps["devices"] = {"ok": bool(mine) and mine.get("deviceName") == args.device,
                            "listed": bool(mine), "org": (mine or {}).get("orgName"), "devices": len(listed or [])}
        sent = account.view().org_policy
        answer = account.api_get("/api/orgs/%s/app-policy" % account.view().org_id).get("data")
        organization = OrganizationPolicy(account, folder)
        managed_policy.set_organization_source(organization.current)
        effective = managed_policy.current()
        organization.remember(account.view())
        switches = {"allowPersonalConnections": "allow_personal_connections", "allowLocalCliAccess":
                    "allow_local_cli_access", "allowVoice": "allow_voice", "allowCollaborators": "allow_collaborators"}
        machine = managed_policy.machine()
        applied = sent is not None and all(getattr(effective, name) == (getattr(machine, name) and sent[key])
                                           for key, name in switches.items())
        steps["org_policy"] = {
            "ok": sent is not None and isinstance(answer, dict) and set(answer) == set(switches) | {"adminContact"}
            and org_policy(answer) == sent and applied and os.path.exists(organization.path),
            "in_token_answer": sent is not None, "fields": sorted(answer) if isinstance(answer, dict) else None,
            "switches": {key: (sent or {}).get(key) for key in switches},
            "admin_contact_set": bool((sent or {}).get("adminContact")), "equals_app_policy_api": org_policy(answer) == sent,
            "applied": applied, "kept_for_offline": os.path.exists(organization.path)}
        if args.end_with == "devices":
            email, org_id = account.view().email, account.view().org_id
            if not args.organization:  # The next link names the organization this sign-in chose.
                policies.append(Policy(organization=org_id, keys=frozenset({"Organization"})))
            stale = account.access_token()
            status, _answer, _data = http_transport("DELETE", console + "/api/me/devices/" + claims["gid"],
                                                    {"Authorization": "Bearer " + stale, "Accept": "application/json"})
            try:
                account.access_token(stale=stale)
                ended, reason = None, None
            except SignInRequired as error:
                ended, reason = str(error.message), getattr(error, "reason", None)
            wait(gone, 30)
            organization.remember(account.view())
            steps["signed_out_from_devices"] = {
                "ok": status == 200 and reason == "signed_out" and ended is not None
                and "signed out of your Angelo team" in ended and account.view().status == "signed_out"
                and gone() and not os.path.exists(organization.path),
                "console_status": status, "error_reason": reason, "app_says": ended,
                "store_items_gone": gone(), "org_policy_forgotten": not os.path.exists(organization.path)}
            again = account.begin_sign_in(device=args.device)
            query = {k: v[0] for k, v in parse_qs(urlsplit(again).query).items()}
            steps["next_sign_in_hints"] = {
                "ok": query.get("login_hint") == email and query.get("org_hint", "") == org_id,
                "login_hint_is_ended_account": query.get("login_hint") == email,
                "org_hint_is_the_organization": query.get("org_hint", "") == org_id, "opened": args.look_again > 0}
            if args.look_again > 0:
                print("Opened the next sign-in link: check the organization is pre-selected.", flush=True)
                if sys.platform == "darwin":
                    subprocess.run(["open", again], check=False)
                wait(lambda: account.view().status != "signing_in", args.look_again, 0.5)
                allowed = wait(lambda: account.view().signed_in, 30)
                steps["next_sign_in_hints"]["allowed"] = allowed
                if allowed:
                    steps["next_sign_in_hints"]["signed_in_to_hinted_org"] = account.view().org_id == org_id
                    steps["next_sign_in_hints"]["ok"] &= account.view().org_id == org_id
            if account.view().status == "signing_in":
                account.cancel_sign_in()
            return report
        last = account._refresh_token
        account.sign_out()
        wait(gone, 30)
        body, headers = form_body({"grant_type": "refresh_token", "refresh_token": last, "client_id": CLIENT_ID})
        status, _answer, data = http_transport("POST", console + "/api/oauth/token", headers, body)
        refused = status == 400 and json.loads(data).get("error") == "invalid_grant"
        steps["sign_out"] = {"ok": refused and gone(), "refresh_refused_after": refused, "store_items_gone": gone()}
        return report
    except SignInRequired as error:
        steps["unexpected_sign_out"] = {"ok": False, "message": str(error.message)}
        return report
    except Exception as error:  # noqa: BLE001 - recorded; the finally block still signs out and cleans up.
        steps["script_error"] = {"ok": False, "error": "%s: %s" % (type(error).__name__, str(error)[:300])}
        return report
    finally:
        if account.view().signed_in:
            account.sign_out()
            wait(gone, 30)
        account.close()
        report["store_items_left"] = not gone()
        report["ok"] = all(step.get("ok") is not False for step in steps.values()) and bool(steps)
        Path(args.report).write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
