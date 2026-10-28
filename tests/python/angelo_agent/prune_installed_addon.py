# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Make a build folder's installed Angelo add-on the checkout's again, before the native suites test it.

An incremental install copies the add-on over the previous one and never deletes a module the checkout no longer
has. A build machine that built another branch in between therefore holds modules this commit does not, and
``run_native_suites.py`` (rightly) refuses the app: a Windows build machine on 2026-10-25 kept 22 parallel-edit modules
from an earlier branch, and 1.0.5 removed such files by hand (release kit ``prune_deleted_addon.ps1``).

This removes exactly the add-on files ``native_addon_guard.check`` reports as "only in the app", with their
bytecode, and nothing else. Any other difference (a file that differs, or one only in the checkout) is a build
problem, left for the guard to refuse. The release stage runs it on the build folder's app only; the MSI is packed
from its own staging folder and is never touched. Standard library only.

    python prune_installed_addon.py --binary <app executable> --source <checkout>
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_addon_guard as guard  # noqa: E402

STALE = "only in the app"


def prune(binary, source_root):
    """Remove the app's add-on files the checkout lacks; ``(removed keys, the guard's result afterwards)``."""
    before = guard.check(binary, source_root)
    scripts = guard.bundled_scripts(binary)
    removed = []
    if scripts is None:
        return removed, before
    addon = scripts / "addons_core" / "angelo_agent"
    for line in before["differing"]:
        key, _, reason = line.partition(": ")
        if reason != STALE or not key.startswith("addon/"):
            continue
        path = addon / key[len("addon/"):]
        if not path.is_file() or addon.resolve() not in path.resolve().parents:
            continue
        path.unlink()
        removed.append(key)
        cache = path.parent / "__pycache__"
        if cache.is_dir():
            for compiled in cache.glob(path.stem + ".*.pyc"):
                compiled.unlink()
    return removed, guard.check(binary, source_root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", required=True, help="the app's executable (Angelo.exe, or Angelo.app/Contents/MacOS/Angelo)")
    parser.add_argument("--source", required=True, help="the checkout whose add-on the app must hold")
    args = parser.parse_args(argv)
    removed, after = prune(args.binary, args.source)
    for key in removed:
        print("removed %s (%s)" % (key, STALE))
    state = "matches the checkout" if after["matches"] else "still differs: " + "; ".join(after["differing"][:guard.LISTED])
    print("%d stale add-on file(s) removed; the app's add-on %s" % (len(removed), state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
