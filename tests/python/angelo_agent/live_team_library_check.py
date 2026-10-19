# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""LIVE read-only check of the team library and IP tools against a real Angelo console (not billed).

Runs the app's own modules (``team_account``, ``team_library_client``,
``team_library_transfer``, ``team_library_results``) outside Blender, with
Blender's Python. It opens the system browser at the console's allow screen; a
person signs in there and clicks Allow. The sign-in lives in memory only (no OS
store item is written) and is revoked at the end. Every request is a read:

1. library search, team and personal space (the tools' default page size);
2. the newest team item's details and its thumbnail through its storage link
   (no credentials sent to storage);
3. the team IP list, the most recently changed IP and, when it has one, its
   first character's entity with readiness;
4. the shaped results the agent would see, checked for links and email
   addresses.

The report holds counts, timings, field names and checks, never titles,
descriptions, prompts, links, tokens or codes. Nothing is uploaded.

    <Blender>/Contents/Resources/5.2/python/bin/python3.13 tests/python/angelo_agent/live_team_library_check.py \\
        --console https://console.angelo.studio --report <tmp>/team-library-live.json
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import team_library_results as results  # noqa: E402
from angelo_agent.team_account import TeamAccount  # noqa: E402
from angelo_agent.team_endpoints import parse_origin  # noqa: E402
from angelo_agent.team_errors import TeamError  # noqa: E402
from angelo_agent.team_library_client import TeamLibraryClient  # noqa: E402
from angelo_agent.team_library_files import IMAGE_FORMATS, check_image, sniff  # noqa: E402
from angelo_agent.team_library_transfer import fetch  # noqa: E402


def timed(report, name, call, default=None):
    """``call()``'s value; a console refusal is recorded (code, status, the console's message) and ``default`` returned."""
    started = time.perf_counter()
    try:
        return call()
    except TeamError as error:
        report["refusals"][name] = {"code": error.code, "status": error.status, "message": error.message}
        return default
    finally:
        report["timings_ms"][name] = round((time.perf_counter() - started) * 1000, 1)


def clean(value):
    text = json.dumps(value)
    return "http" not in text and "@" not in text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--console", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600, help="seconds to wait for Allow in the browser")
    args = parser.parse_args(argv)
    origin = parse_origin(args.console, "--console")
    report = {"console": origin.url, "timings_ms": {}, "checks": {}, "counts": {}, "fields": {}, "refusals": {},
              "success": False}
    account = TeamAccount(origin.url, sign_in_timeout=args.timeout)
    try:
        url = account.begin_sign_in(device="Angelo team-library check")
        subprocess.run(["open", url], check=False)
        print("Opened the console's allow screen; sign in and click Allow (waiting up to %d s)." % args.timeout, flush=True)
        deadline = time.monotonic() + args.timeout + 30
        while not account.view().signed_in and time.monotonic() < deadline:
            if account.view().status == "signed_out" and account.view().message:
                raise SystemExit("Sign-in ended: %s" % account.view().message)
            time.sleep(0.2)
        view = account.view()
        if not view.signed_in:
            raise SystemExit("No sign-in within the timeout.")
        report["checks"]["console_scope"] = "api" in view.scopes
        report.update(seat=view.seat, scopes=sorted(view.scopes))
        client = TeamLibraryClient(account)

        team, cursor = timed(report, "library_search_team", lambda: client.search(space="team", limit=10), ([], None))
        personal, _ = timed(report, "library_search_personal", lambda: client.search(space="personal", limit=10),
                            ([], None))
        stats = timed(report, "library_stats", client.stats, {})
        report["counts"].update(library_items=stats.get("itemCount"), quota_bytes=stats.get("quotaBytes"),
                                used_bytes=stats.get("usedBytes"))
        report["counts"].update(team_page=len(team), team_more=cursor is not None, personal_page=len(personal),
                                kinds=sorted({i.get("kind") for i in team + personal}))
        shaped = [results.item(i, view.user_id) for i in team + personal]
        report["checks"]["search_results_have_no_links_or_emails"] = clean(shaped)
        if team:
            report["fields"]["library_item"] = sorted(team[0])
            item = timed(report, "library_get", lambda: client.item(team[0]["id"]), {})
            report["checks"]["item_shaped_without_links"] = clean(results.item(item, view.user_id, full=True))
            if item.get("thumbnailUrl"):
                data = timed(report, "thumbnail_fetch", lambda: fetch(item["thumbnailUrl"], limit=5 * 1024 * 1024))
                fmt = sniff(data[:65536])
                report["checks"]["thumbnail_is_an_admitted_image"] = fmt in IMAGE_FORMATS and bool(check_image(data))
                report["counts"]["thumbnail_bytes"] = len(data)

        ips = timed(report, "ips_list_team", lambda: client.ips(space="team"), [])
        personal_ips = timed(report, "ips_list_personal", lambda: client.ips(space="personal"), [])
        report["counts"]["personal_ips"] = len(personal_ips)
        report["counts"]["team_ips"] = len(ips)
        report["checks"]["ips_shaped_without_links"] = clean([results.ip(i) for i in ips])
        if ips:
            newest = max(ips, key=lambda i: str(i.get("updatedAt", "")))
            report["fields"]["ip"] = sorted(newest)
            detail = timed(report, "ip_get", lambda: client.ip(newest["id"]), {})
            entities = detail.get("entities") or []
            report["counts"].update(entities=len(entities), media=len(detail.get("media") or []),
                                    entity_kinds=sorted({e.get("kind") for e in entities}))
            character = next((e for e in entities if e.get("kind") == "character"), None)
            if character:
                entity = timed(report, "ip_entity", lambda: client.entity(newest["id"], character["id"]), {})
                report["fields"]["entity"] = sorted(entity)
                readiness = results.readiness(entity.get("readiness"))
                report["checks"]["character_readiness_present"] = readiness is not None
                report["counts"]["character_ready"] = bool(readiness and readiness.get("ready"))
                report["checks"]["entity_shaped_without_links"] = clean(results.entity(entity))
        # A refusal is evidence, not a failure of the check; the report lists each with the console's message.
        report["success"] = all(report["checks"].values())
    finally:
        account.sign_out()
        time.sleep(2.0)  # The revoke runs on the account's worker (one ~0.2 s request); let it finish.
        account.close()
        args.report.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
