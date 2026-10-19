# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit read-only endpoint/TLS smoke. No keys, auth or paid requests."""

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.provider_registry import HTTP_MODES, SPECS
from angelo_agent.api_connections import _NoRedirect
from angelo_agent.team_http import tls_context


def check(mode):
    spec = SPECS[mode]
    request = urllib.request.Request(spec["base_url"] + spec["probe"],
                                     headers={"Accept": "application/json"}, method="GET")
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=tls_context()))
    try:
        with opener.open(request, timeout=12) as response:
            status = response.status
        return {"mode": mode, "tls": True, "http_status": status}
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        return {"mode": mode, "tls": True, "http_status": status}
    except Exception as error:
        return {"mode": mode, "tls": False, "error_type": type(error).__name__}


with ThreadPoolExecutor(max_workers=7) as pool:
    results = list(pool.map(check, sorted(m for m in HTTP_MODES if SPECS[m]["probe"])))
report = {"success": all(r["tls"] for r in results), "credentials_sent": False,
          "billable_requests": 0, "results": results,
          "note": "Only endpoint reachability and Blender's TLS trust store; not account/model verification"}
Path(os.environ.get("ANGELO_PROVIDER_QA_REPORT") or Path(tempfile.gettempdir(), "angelo-blender-api-tls.json")).write_text(json.dumps(report, indent=2))
print("ANGELO_BLENDER_API_TLS", json.dumps(report), flush=True)
