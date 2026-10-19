# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Windows checks of the team account client, against the fake console (no network, no account).

Run with the app's own Python on a Windows machine, for example the installed
``<install>\\5.2\\python\\bin\\python.exe``. ``scripts/angelo/windows_teams_validate.ps1`` does so on the
validation machine. Nothing here talks to a real console or provider.

What it proves on Windows (each is a step in the report):

* ``credential_manager``: the team sign-in and grant key live in Windows Credential Manager under the item kinds
  named from the bundle identifier, listed by ``cmdkey /list`` without the secret, and a write at the size limit
  works while one above it is refused cleanly;
* ``sign_in``: the PKCE loopback sign-in completes, the loopback server listens on 127.0.0.1 only, and the
  firewall gains no rule; with ``--browser`` the loopback address is opened in the default browser, whose
  page is captured (``--screens``);
* ``refresh_single_flight``: four callers share one refresh; two more processes restore the same stored sign-in
  at the same moment through ``team_lock`` (``msvcrt.locking``) and nothing is revoked;
* ``lock_crash_release``: the lock is released when its holder is killed;
* ``policy_*``: Group Policy registry values (HKLM over HKCU, 64-bit view, every value type, unusable values read
  restrictively) become the policy the app enforces;
* ``sign_out_wipe``: signing out revokes the grant, deletes both Credential Manager items and forgets the
  organization's policy.

    python.exe scripts/angelo/windows_team_checks.py --report <dir>\\team-windows.json [--browser] [--screens <dir>]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_CHILD = """
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from angelo_agent import team_lock, team_token_store
from angelo_agent.team_account import TeamAccount
from angelo_agent.managed_policy import Policy
console, profile, folder, go = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
backend, _ = team_token_store.open_backend({}, sys.platform, False)
vault = team_token_store.TokenVault(backend, console, profile)
account = TeamAccount(console, vault=vault, process_lock=team_lock.factory(folder), policy=Policy)
while not os.path.exists(go):
    time.sleep(0.005)
account.restore()
deadline = time.monotonic() + 60
while account.view().status == "restoring" and time.monotonic() < deadline:
    time.sleep(0.01)
account.drain()
print(json.dumps({"status": account.view().status, "message": str(account.view().message)}))
"""

_HOLDER = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
from angelo_agent import team_lock
with team_lock.held(sys.argv[2]):
    open(sys.argv[3], "w").write("held")
    time.sleep(120)
"""

_SCREENSHOT = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bitmap.Save($args[0], [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose(); $bitmap.Dispose()
"""


def wait(predicate, seconds, step=0.1):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def powershell(script, *args, timeout=60):
    run = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script, *args],
                         capture_output=True, text=True, timeout=timeout)
    return run.returncode, run.stdout, run.stderr


def screenshot(path):
    """A PNG of the whole interactive desktop (needs a signed-in session); False when it can't be taken."""
    script = _SCREENSHOT.replace("$args[0]", "'%s'" % str(path).replace("'", "''"))
    code, _out, _err = powershell(script)
    return code == 0 and Path(path).is_file()


def credential_targets():
    """Target names ``cmdkey /list`` shows (never a secret: the command doesn't print one)."""
    run = subprocess.run(["cmdkey.exe", "/list"], capture_output=True, text=True)
    return [line.split("target=", 1)[1].strip() for line in run.stdout.splitlines() if "target=" in line]


def listening(port):
    run = subprocess.run(["netstat.exe", "-ano", "-p", "tcp"], capture_output=True, text=True)
    return [line.split()[1] for line in run.stdout.splitlines() if "LISTENING" in line and re.search(r":%d\s" % port, line)]


def firewall_rules():
    run = subprocess.run(["netsh.exe", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in"],
                         capture_output=True, text=True)
    return sorted(line.split(":", 1)[1].strip() for line in run.stdout.splitlines() if line.startswith("Rule Name:"))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--report", required=True)
    parser.add_argument("--addons", default=str(ROOT / "scripts/addons_core"),
                        help="the add-on folder to test (default: this checkout's; the installed app's for a shipped check)")
    parser.add_argument("--fakes", default=str(ROOT / "tests/python/angelo_agent"), help="the folder holding fake_team_console.py")
    parser.add_argument("--browser", action="store_true", help="open the loopback address in the default browser")
    parser.add_argument("--screens", default="", help="folder for screenshots of the browser page (needs a desktop session)")
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("These checks are for Windows.")
    sys.path.insert(0, args.addons)
    sys.path.insert(0, args.fakes)

    import winreg
    from urllib.request import Request, urlopen
    from urllib.parse import parse_qs, urlsplit

    from angelo_agent import key_store, managed_policy, team_lock, team_token_store  # noqa: E402
    from angelo_agent.saved_keys import profile_id  # noqa: E402
    from angelo_agent.team_account import TeamAccount  # noqa: E402
    from angelo_agent.team_http import form_body, http_transport  # noqa: E402
    from angelo_agent.team_signin import CLIENT_ID  # noqa: E402
    from fake_team_console import FakeConsole  # noqa: E402

    report = {"python": sys.version.split()[0], "addons": args.addons, "steps": {}}
    steps = report["steps"]
    folder = tempfile.mkdtemp(prefix="angelo-team-win-")
    profile = profile_id(folder)
    fake = FakeConsole()
    console = fake.url
    created_policy_keys = []
    account = None

    def step(name, ok, **detail):
        steps[name] = {"ok": ok, **detail}
        print("%s  %s" % ("PASS" if ok else "FAIL" if ok is False else "NOTE", name), flush=True)

    try:
        # 1. Credential Manager -------------------------------------------------------------------------------
        backend, reason = team_token_store.open_backend(os.environ, sys.platform, False)
        grant_backend, _ = team_token_store.open_backend(os.environ, sys.platform, False, team_token_store.GRANT_SERVICE,
                                                         team_token_store.GRANT_COMMENT)
        if backend is None:
            step("credential_manager", False, reason=reason)
            return report
        vault = team_token_store.TokenVault(backend, console, profile)
        grant_vault = team_token_store.TokenVault(grant_backend, console, profile, label="Angelo team grant key")
        probe = team_token_store.TokenVault(backend, console, profile + "-probe")
        secret_ok, big_ok, over_kind = "x" * 600, "y" * 2000, ""
        probe.write(secret_ok)
        round_trip = probe.read() == secret_ok
        probe.write(big_ok)
        big_trip = probe.read() == big_ok
        try:
            probe.write("z" * 2700)   # Above CRED_MAX_CREDENTIAL_BLOB_SIZE (2560 bytes).
            over_kind = "accepted"
        except key_store.KeyStoreError as error:
            over_kind = error.kind
        listed = credential_targets()
        mine = [t for t in listed if t.startswith(team_token_store.ACCOUNT_SERVICE + ":")]
        probe.delete()
        step("credential_manager", round_trip and big_trip and bool(mine) and probe.read() is None
             and over_kind == "too_long" and key_store.store_text().name == "Credential Manager",
             store=key_store.store_text().name, service=backend.service, grant_service=grant_backend.service,
             round_trip=round_trip, two_kb_trip=big_trip, over_limit=over_kind, listed_by_cmdkey=bool(mine),
             deleted=probe.read() is None)

        # 2. Sign-in through the loopback server --------------------------------------------------------------
        before_rules = firewall_rules()
        account = TeamAccount(console, vault=vault, grant_vault=grant_vault, process_lock=team_lock.factory(folder),
                              policy=managed_policy.Policy)
        url = account.begin_sign_in(device="Angelo Windows check")
        redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0]
        port = urlsplit(redirect).port
        addresses = listening(port)
        callback = fake.callback(url)
        shot = ""
        if args.browser:
            os.startfile(callback)  # The system default browser, as the app's own button does.
            time.sleep(7)
            if args.screens:
                Path(args.screens).mkdir(parents=True, exist_ok=True)
                shot = str(Path(args.screens) / "loopback-sign-in-browser.png")
                shot = shot if screenshot(shot) else ""
        else:
            urlopen(Request(callback), timeout=20).read()
        wait(lambda: account.view().signed_in and account.view().providers is not None, 30)
        view = account.view()
        after_rules = firewall_rules()
        targets = credential_targets()
        step("sign_in", view.signed_in and view.stored == "saved" and vault.read() == account._refresh_token
             and addresses == ["127.0.0.1:%d" % port] and after_rules == before_rules,
             status=view.status, stored=view.stored, listening=addresses, port=port, browser=args.browser,
             screenshot=shot, firewall_rules_unchanged=after_rules == before_rules,
             team_items=[t for t in targets if "team-" in t])

        # 3. Single flight: threads, then two processes ---------------------------------------------------------
        access = account.access_token()
        before = vault.read()
        refreshes_before = len(fake.refresh_requests())
        results = []
        threads = [threading.Thread(target=lambda: results.append(account.access_token(stale=access))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        after = vault.read()
        step("refresh_single_flight_threads", len(set(results)) == 1 and results[0] != access and after != before
             and len(fake.refresh_requests()) - refreshes_before == 1,
             callers=len(results), distinct=len(set(results)), console_refreshes=len(fake.refresh_requests()) - refreshes_before)
        go = os.path.join(folder, "go")
        children = [subprocess.Popen([sys.executable, "-s", "-c", _CHILD, args.addons, console, profile, folder, go],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        time.sleep(1.5)
        Path(go).touch()
        outcomes = []
        for child in children:
            out, err = child.communicate(timeout=120)
            lines = out.strip().splitlines()
            outcomes.append(json.loads(lines[-1]) if lines else {"status": "no output", "message": err[-300:]})
        account.access_token(stale=results[0] if results else access)
        step("refresh_single_flight_processes", all(o["status"] == "signed_in" for o in outcomes) and not fake.revoked
             and account.view().signed_in, children=outcomes, revoked=dict(fake.revoked))

        # 4. The lock is released when its holder dies ---------------------------------------------------------
        marker = os.path.join(folder, "holder")
        holder = subprocess.Popen([sys.executable, "-s", "-c", _HOLDER, args.addons, folder, marker])
        wait(lambda: os.path.exists(marker), 30)
        blocked = False
        try:
            with team_lock.held(folder, timeout=0.5):
                pass
        except team_lock.LockTimeout:
            blocked = True
        holder.kill()
        holder.wait(30)
        started = time.monotonic()
        try:
            with team_lock.held(folder, timeout=10):
                acquired_in = round(time.monotonic() - started, 3)
        except team_lock.LockTimeout:
            acquired_in = None
        step("lock_crash_release", blocked and acquired_in is not None and acquired_in < 5,
             second_process_blocked_while_held=blocked, acquired_after_kill_seconds=acquired_in)

        # 5. Grant key in its own item ------------------------------------------------------------------------
        if account.view().can_generate:
            account._enqueue("grant_key")
            wait(lambda: account.grant_key() or account.view().grant_error, 30)
            targets = credential_targets()
            step("grant_key_item", bool(account.grant_key()) and any(t.startswith(team_token_store.GRANT_SERVICE + ":") for t in targets),
                 items=[t.split(":")[0] for t in targets if "team-" in t])
        else:
            step("grant_key_item", None, skipped="the fake sign-in has no generate scope")

        # 6. Group Policy registry values --------------------------------------------------------------------
        base = managed_policy.WINDOWS_KEY
        org = str(uuid.uuid4())
        pem = os.path.join(folder, "roots.pem")
        Path(pem).write_text("-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n")

        def put(hive, values, view=winreg.KEY_WOW64_64KEY):
            key = winreg.CreateKeyEx(hive, base, 0, winreg.KEY_WRITE | view)
            created_policy_keys.append((hive, view))
            for name, (kind, value) in values.items():
                winreg.SetValueEx(key, name, 0, kind, value)
            winreg.CloseKey(key)

        def clear():
            for hive, view in created_policy_keys:
                try:
                    winreg.DeleteKeyEx(hive, base, view, 0)
                except OSError:
                    pass
            created_policy_keys.clear()

        def sz(value):
            return winreg.REG_SZ, value

        def dword(value):
            return winreg.REG_DWORD, value

        clear()
        none = managed_policy.load()
        put(winreg.HKEY_LOCAL_MACHINE, {
            "ConsoleURL": sz("https://console.example.test"), "GatewayURL": sz("https://gateway.example.test"),
            "RequireTeamSignIn": dword(1), "AllowPersonalConnections": dword(0), "AllowVoice": sz("false"),
            "AllowCollaborators": dword(0), "AllowLocalCLIAccess": dword(1), "AdminContact": sz("it@example.test"),
            "Organization": sz(org), "EmailDomains": (winreg.REG_MULTI_SZ, ["example.test", "Northwind.Example"]),
            "ExtraCACertificates": sz(pem)})
        policy = managed_policy.load()
        right = managed_policy.admit(policy, org, "a@example.test")
        wrong_org = str(managed_policy.admit(policy, str(uuid.uuid4()), "a@example.test"))
        wrong_mail = str(managed_policy.admit(policy, org, "a@gmail.com"))
        step("policy_hklm_every_value_type",
             not none.managed and policy.console_url == "https://console.example.test" and policy.gateway_url == "https://gateway.example.test"
             and policy.require_team_sign_in and not policy.allow_personal_connections and not policy.allow_voice
             and not policy.allow_collaborators and policy.allow_local_cli_access and policy.admin_contact == "it@example.test"
             and policy.organization == org and policy.email_domains == ("example.test", "northwind.example")
             and policy.extra_ca_certificates == pem and not policy.problems and len(policy.keys) == 11
             and right is None and "different Angelo team" in wrong_org and "work account" in wrong_mail
             and policy.team_only and "only its Angelo team" in str(managed_policy.personal_refusal(policy, True))
             and "it@example.test" in str(managed_policy.contact(policy)),
             sources=list(policy.sources), keys=sorted(policy.keys), problems=list(policy.problems),
             wrong_organization=wrong_org, wrong_email=wrong_mail)
        clear()

        put(winreg.HKEY_LOCAL_MACHINE, {"ConsoleURL": sz("https://machine.example.test"), "AllowVoice": dword(0)})
        put(winreg.HKEY_CURRENT_USER, {"ConsoleURL": sz("https://user.example.test"), "AllowVoice": dword(1),
                                       "AdminContact": sz("user-level@example.test")})
        layered = managed_policy.load()
        step("policy_hklm_over_hkcu",
             layered.console_url == "https://machine.example.test" and not layered.allow_voice
             and layered.admin_contact == "user-level@example.test" and len(layered.sources) == 2,
             sources=list(layered.sources), console_url=layered.console_url, allow_voice=layered.allow_voice,
             admin_contact=layered.admin_contact)
        clear()

        put(winreg.HKEY_LOCAL_MACHINE, {
            "RequireTeamSignIn": dword(7), "AllowVoice": sz("maybe"), "ConsoleURL": sz("http://insecure.example.test"),
            "Organization": sz("not-a-uuid"), "EmailDomains": (winreg.REG_MULTI_SZ, ["bad domain"]),
            "AllowCollaborators": (winreg.REG_BINARY, b"\x01")})
        bad = managed_policy.load()
        problem = str(managed_policy.sign_in_problem(bad) or "")
        step("policy_unusable_values_are_restrictive",
             bad.require_team_sign_in and not bad.allow_voice and bad.console_url == managed_policy.INVALID
             and bad.organization == managed_policy.INVALID and bad.email_domains == (managed_policy.INVALID,)
             and {"RequireTeamSignIn", "AllowVoice", "ConsoleURL", "Organization", "EmailDomains"} <= set(bad.problems)
             and "ConsoleURL" in problem and bad.allow_collaborators,
             problems=list(bad.problems), sign_in_problem=problem, binary_value_ignored=bad.allow_collaborators)
        clear()

        put(winreg.HKEY_LOCAL_MACHINE, {"AdminContact": sz("from the 32-bit view")}, winreg.KEY_WOW64_32KEY)
        seen = managed_policy.load()
        step("policy_is_one_key_for_32_and_64_bit", seen.admin_contact == "from the 32-bit view",
             seen_32bit_value=bool(seen.admin_contact),
             note="HKLM\\SOFTWARE\\Policies is a shared registry key: a 32-bit and a 64-bit program read the same values")
        clear()

        os.environ["ANGELO_QA_PEM"] = pem
        put(winreg.HKEY_LOCAL_MACHINE, {"ExtraCACertificates": (winreg.REG_EXPAND_SZ, r"%ANGELO_QA_PEM%")})
        expanded = managed_policy.load()
        step("policy_expand_sz_path", None if not expanded.extra_ca_certificates else True,
             accepted=bool(expanded.extra_ca_certificates), problems=list(expanded.problems),
             note="a REG_EXPAND_SZ path (Group Policy's usual type for paths) is read unexpanded: "
                  + ("expanded" if expanded.extra_ca_certificates else "it counts as unusable and adds no roots"))
        clear()

        # 7. Sign out: revoke, wipe, forget ---------------------------------------------------------------------
        last = account._refresh_token
        grant_item = [t for t in credential_targets() if t.startswith(team_token_store.GRANT_SERVICE + ":")]
        account.sign_out()
        wait(lambda: vault.read() is None and grant_vault.read() is None, 30)
        remaining = [t for t in credential_targets() if "team-sign-in" in t or "team-grant-key" in t]
        body, headers = form_body({"grant_type": "refresh_token", "refresh_token": last, "client_id": CLIENT_ID})
        status, _answer, data = http_transport("POST", console + "/api/oauth/token", headers, body)
        refused = status == 400 and json.loads(data).get("error") == "invalid_grant"
        step("sign_out_wipe", refused and not remaining and account.view().status == "signed_out" and bool(fake.revoked),
             refresh_refused_after=refused, items_before=len(grant_item), team_items_left=remaining,
             revoked=dict(fake.revoked), status=account.view().status)
        return report
    except Exception as error:  # noqa: BLE001 - recorded; the finally block cleans up.
        steps["script_error"] = {"ok": False, "error": "%s: %s" % (type(error).__name__, str(error)[:400])}
        return report
    finally:
        for hive, view in created_policy_keys:
            try:
                winreg.DeleteKeyEx(hive, managed_policy.WINDOWS_KEY, view, 0)
            except OSError:
                pass
        if account is not None:
            try:
                if account.view().signed_in:
                    account.sign_out()
                account.close()
            except Exception:  # noqa: BLE001 - best effort.
                pass
        fake.close()
        report["ok"] = bool(steps) and all(s.get("ok") is not False for s in steps.values())
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2))
        print(json.dumps({"ok": report["ok"], "steps": {k: v.get("ok") for k, v in steps.items()}}, indent=2), flush=True)


if __name__ == "__main__":
    sys.exit(0 if main().get("ok") else 1)
