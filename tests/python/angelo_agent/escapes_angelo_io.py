# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own network, TLS and logging paths against what a script changes in the process (the reviewer's fourth
read, MUST-3 b/c, 2026-11-16), for test_blender_confinement_escapes. Each check judges the class:

* Angelo's TLS context is never built from what a script changed (a factory rebound while it runs), and a context a
  script changed in place makes Angelo refuse, saying so, rather than connect.
* Handlers a script adds to Angelo's loggers or the root logger are gone when it ends.
* A script's own use of logging and urllib still works (scripts don't use the network: urllib's parsing).
"""

import logging
import ssl


def _team_http():
    from angelo_agent import team_http
    getattr(team_http, "prepare_tls", lambda: None)()   # Built before any script, where the guard does so.
    return team_http


def tls_steered_while_running(run, check):
    team_http = _team_http()
    held = {}
    outcome = run("import ssl\n"
                  "ssl.create_default_context = lambda *a, **k: ssl._create_unverified_context()\n"
                  "try:\n    import sys\n    team_http = sys.modules['angelo_agent.team_http']\n"
                  "    context = team_http.tls_context()\n"
                  "    HELD['context'] = (int(context.verify_mode), context.check_hostname)\n"
                  "except Exception as error:\n    HELD['refused'] = str(error)\n", HELD=held)
    # Verified, refused as changed, or Angelo's code not reachable at all (its modules are opaque to scripts).
    refused = held.get("refused", "")
    verified = held.get("context") == (int(ssl.CERT_REQUIRED), True) or "changed" in refused or \
        "isn't open to scripts" in refused
    check("angelo_tls_never_built_from_script_state", outcome["refused"] is None and verified, (outcome, held))


def tls_changed_in_place(run, check):
    team_http = _team_http()
    outcome = run("import ssl, sys\nteam_http = sys.modules['angelo_agent.team_http']\ncontext = team_http.tls_context()\n"
                  "context.check_hostname = False\ncontext.verify_mode = ssl.CERT_NONE\n")
    try:
        context = team_http.tls_context()
        after = ("verifies" if context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
                 else "unverified")
    except ssl.SSLError as error:
        after = "refused: " + str(error)
    team_http._SHARED = ()   # The next check builds a fresh one.
    check("angelo_tls_changed_in_place_refused", outcome["refused"] is None and
          (after == "verifies" or "changed" in after), (outcome, after))


def loggers(run, check):
    angelo = logging.getLogger("angelo_agent.escapes_angelo_io")
    before = (list(logging.root.handlers), list(angelo.handlers), logging.root.level)
    outcome = run("import io, logging\n"
                  "logging.root.addHandler(logging.StreamHandler(io.StringIO()))\n"
                  "logging.getLogger(NAME).addHandler(logging.StreamHandler(io.StringIO()))\n"
                  "logging.root.setLevel(logging.DEBUG)\n", NAME=angelo.name)
    after = (list(logging.root.handlers), list(angelo.handlers), logging.root.level)
    same = len(after[0]) == len(before[0]) and all(a is b for a, b in zip(after[0], before[0])) and \
        not after[1] and after[2] == before[2]
    logging.root.handlers[:] = before[0]
    angelo.handlers[:] = before[1]
    logging.root.setLevel(before[2])
    check("loggers_as_found", outcome["refused"] is None and same, (outcome, len(before[0]), len(after[0]),
                                                                    len(after[1]), after[2]))


def ordinary_io(run, check):
    held = {}
    outcome = run("import io, logging, urllib.parse\n"
                  "stream = io.StringIO()\nlogger = logging.getLogger('a_script')\n"
                  "logger.addHandler(logging.StreamHandler(stream))\nlogger.warning('noted')\n"
                  "HELD['log'] = stream.getvalue()\n"
                  "HELD['data'] = urllib.parse.unquote('%68ello')\n", HELD=held)
    check("ordinary_logging_and_urllib", outcome["refused"] is None and held.get("data") == "hello" and
          "noted" in held.get("log", ""), (outcome, held))


def run_all(run, check):
    tls_steered_while_running(run, check)
    tls_changed_in_place(run, check)
    loggers(run, check)
    ordinary_io(run, check)
