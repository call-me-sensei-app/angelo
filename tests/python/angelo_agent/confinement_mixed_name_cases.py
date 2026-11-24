# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent code is the agent's by birth, whatever names it carries (test_blender_script_confinement's mixed-name checks;
BREAKIT break-it-confine-depth-5dc512c07e1 MUST-A).

The compiler never gives nested code a different file name from its parent, but marshal does: a script compiles a
function under the agent's own name, marshals it, re-labels the nested function's file name in the bytes (``<string>``
or an installed module's path), and unmarshals it with importlib's ``_compile_bytecode``. With taint decided by name,
the nested code was in no set, the region's end took it for installed code, and a function made from it read a
private file after the region. A script's code is now counted when a confined thread makes it. Each variant is left
in a tuple and in a handler list, and called by Angelo's code after the region; each must be refused PRIVATE. A
lambda chain 300 deep checks that no depth bound came back. The suite runs these last: code minted under an
installed file's name may make every hot path ask for the rest of the session (failing closed), which later
"free" checks would read as a leak. Run only inside Blender, from the suite.
"""

import functools

MARK = "<angelo-reviewed-script>"

RELABEL = """import marshal, importlib._bootstrap_external as e
outer = compile("def f(*a):\\n    return open(KEY).read()\\n", MARK, "exec")
raw = marshal.dumps(outer, 2)
old = b"u" + len(MARK).to_bytes(4, "little") + MARK.encode()
new = b"u" + len(NAME.encode()).to_bytes(4, "little") + NAME.encode()
i = raw.index(old)
code = e._compile_bytecode(raw[:i] + new + raw[i + len(old):], "m", "x", MARK)
ns = {"KEY": KEY, "open": open}
exec(code, ns)
held["names"] = (code.co_filename, ns["f"].__code__.co_filename)
held["tuple"] = (ns["f"],)
bpy.app.handlers.depsgraph_update_post.append(ns["f"])
held["handler"] = len(bpy.app.handlers.depsgraph_update_post) - 1
"""

DEEP = """ns = {"KEY": KEY, "open": open}
exec("f = " + "lambda *a: " * 300 + "open(KEY).read()", ns)
g = ns["f"]
for _ in range(299):
    g = g()
held["tuple"] = (g,)
"""


def _called(check, name, call, PRIVATE, ScriptConfined):
    try:
        read = call()
        check(name, False, "read the private key: %d characters" % len(read))
    except ScriptConfined as error:
        check(name, error.reason == PRIVATE, error.reason)
    except Exception as error:   # noqa: BLE001 - reported as the check's detail.
        check(name, False, repr(error))


def run_mixed_name_cases(run, check, PRIVATE, ScriptConfined, gc):
    import bpy
    handlers = bpy.app.handlers.depsgraph_update_post
    for label, name in (("string", "<string>"), ("installed", functools.__file__)):
        held = {}
        refused = run(RELABEL, MARK=MARK, NAME=name, held=held).get("refused")   # Its namespace is not kept.
        gc.collect()
        if "tuple" not in held:   # The relabel itself was refused: nothing was left to call, which is confined too.
            check("mixed_name_confined_%s_tuple" % label, refused is not None, refused)
            continue
        check("mixed_name_relabelled_%s" % label, held["names"][1] == name, held["names"])   # The test bites.
        _called(check, "mixed_name_confined_%s_tuple" % label, lambda: held["tuple"][0](), PRIVATE, ScriptConfined)
        _called(check, "mixed_name_confined_%s_handler" % label, lambda: handlers[held["handler"]](None, None),
                PRIVATE, ScriptConfined)
        handlers.pop(held["handler"])
        held.clear()
        gc.collect()
    held = {}
    run(DEEP, held=held)
    gc.collect()
    if "tuple" in held:
        _called(check, "deep_code_confined_after_region_300", lambda: held["tuple"][0](), PRIVATE, ScriptConfined)
    else:
        check("deep_code_confined_after_region_300", False, "the script did not leave it")
    held.clear()
    gc.collect()
