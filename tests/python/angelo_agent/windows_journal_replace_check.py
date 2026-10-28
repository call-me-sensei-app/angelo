# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""On a real Windows host: a chat's store rewrites its file with no handle of its own open (R6).

Not a host unit test (it is not named ``test_*``): it checks what ``test_native_journal_guard`` simulates, on real
NTFS, with the add-on's own modules. Run with any Python 3.13 (the installed app's bundled one is best):

    python -B windows_journal_replace_check.py [--addons <folder holding angelo_agent>]

It reports which replace ran (``replace_path``: ``posix`` or ``classic``, ``win32_replace``).
``--addons`` defaults to this checkout's ``scripts/addons_core``; for an installed app pass its
``<install>\\<version>\\scripts\\addons_core``. Prints one line per check and a JSON summary; exit status 0 only when
every check passed. Writes only under a new temporary folder, removed afterwards.
"""

import argparse
import json
import os
import sys
import tempfile
import traceback
import uuid
from pathlib import Path


def _winerror(error):
    return getattr(error, "winerror", None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--addons", default=str(Path(__file__).resolve().parents[3] / "scripts" / "addons_core"))
    parser.add_argument("--any-os", action="store_true", help="run elsewhere too (a dry run of the script itself)")
    arguments = parser.parse_args()
    if os.name != "nt" and not arguments.any_os:
        print("This check runs on Windows only.")
        return 2
    sys.path.insert(0, arguments.addons)
    from angelo_agent import session_journal, win32_replace, worker_journal_owner
    from angelo_agent.sessions import SessionStore

    results = {}

    def check(name, function):
        try:
            detail = function()
            results[name] = {"ok": True, "detail": detail}
        except Exception as error:   # Reported, never raised: every check runs.
            results[name] = {"ok": False, "detail": "%s: %s" % (type(error).__name__, error),
                             "trace": traceback.format_exc(limit=6)}
        print("%s %s %s" % ("PASS" if results[name]["ok"] else "FAIL", name, results[name]["detail"]))

    with tempfile.TemporaryDirectory(prefix="angelo-r6-") as folder:
        root = Path(folder)

        def model(name):
            """The rule the simulation encodes: what MoveFileExW does to a name that is open."""
            target, source = root / (name + ".json"), root / (name + ".tmp")
            target.write_bytes(b"old\n")
            seen = {}
            for label, opener in (("delete_sharing", lambda: session_journal.open_shared(target)),
                                  ("python_open", lambda: os.open(target, os.O_RDONLY))):
                source.write_bytes(b"new\n")
                fd = opener()
                try:
                    os.replace(source, target)
                    seen[label] = "replaced"
                except PermissionError as error:
                    seen[label] = _winerror(error)
                finally:
                    os.close(fd)
            source.write_bytes(b"new\n")
            os.replace(source, target)
            seen["no_handle"] = "replaced"
            return seen

        def store_rewrites(store_type):
            def run():
                store = store_type(str(root), str(uuid.uuid4()))
                state = store.load()
                writes = []
                for text in ("a", "b", "c"):
                    state["messages"].append({"role": "user", "text": text})
                    store.save(state)
                    writes.append(store.last_write)
                store.compact(state)
                writes.append(store.last_write)
                # A save after a rewrite appends: a rewrite each time means the writer lost the file's identity.
                if writes != ["rewrite", "append", "append", "rewrite"]:
                    raise AssertionError("writes were %s" % writes)
                return writes
            return run

        def reader_defers():
            store = worker_journal_owner.OwnedSessionStore(str(root), str(uuid.uuid4()))
            state = store.load()
            state["messages"].append({"role": "user", "text": "a"})
            store.save(state)
            reader = session_journal.open_shared(store.path)   # A mirror read in progress.
            try:
                store.compact(state)
                during = store.last_write
                state["messages"].append({"role": "user", "text": "b"})
                store.save(state)
            finally:
                os.close(reader)
            store.compact(state)
            if store.last_write != "rewrite":
                raise AssertionError("the compaction did not land after the reader closed")
            if win32_replace.last == win32_replace.POSIX and during != "rewrite":
                raise AssertionError("with POSIX semantics the compaction must land while the reader holds the file")
            return {"during_read": during, "after": store.last_write, "path": win32_replace.last}

        def replace_while_read():
            """The journal's own replace (``session_journal._replace``) lands while a reader holds the target open
            sharing delete, and the reader goes on reading the file it opened: POSIX semantics (``win32_replace``)."""
            target, source = root / "held.json", root / "held.json.new.tmp"
            target.write_bytes(b"old\n")
            source.write_bytes(b"new\n")
            reader = session_journal.open_shared(target)
            try:
                session_journal._replace(source, target, attempts=1)
                kept = os.read(reader, 16)
            finally:
                os.close(reader)
            if win32_replace.last != win32_replace.POSIX:
                raise AssertionError("the replace ran %s: POSIX semantics are unsupported here" % win32_replace.last)
            if target.read_bytes() != b"new\n" or kept != b"old\n":
                raise AssertionError("replaced %r, reader read %r" % (target.read_bytes(), kept))
            return {"path": win32_replace.last, "reader_kept": kept.decode()}

        def identities():
            """Evidence, never a failure: how often a path's ``os.stat`` differs from a descriptor's ``os.fstat``
            right after an append (the lag ``session_journal.stat_of`` keeps out of every identity compared)."""
            target = root / "identity.json"
            target.write_bytes(b"x\n")
            differs, fields = 0, set()
            names = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink", "st_mode")
            for _ in range(200):
                fd = os.open(target, os.O_WRONLY | os.O_APPEND | session_journal.BINARY)
                try:
                    os.write(fd, b"{}\n")
                    os.fsync(fd)
                    by_handle = os.fstat(fd)
                finally:
                    os.close(fd)
                by_path = os.stat(target, follow_symlinks=False)
                changed = {name for name in names if getattr(by_path, name) != getattr(by_handle, name)}
                differs += bool(changed)
                fields |= changed
            stat_of = session_journal.full_identity(session_journal.stat_of(target))
            fd = session_journal.open_shared(target)
            try:
                if stat_of != session_journal.full_identity(os.fstat(fd)):
                    raise AssertionError("stat_of does not report the descriptor's identity")
            finally:
                os.close(fd)
            return {"appends": 200, "path_stat_differed": differs, "fields": sorted(fields)}

        def emoji_folder():
            """A chat saved and compacted in a folder whose name has characters outside the Basic Multilingual Plane
            (two UTF-16 code units each) lands at its own name, nowhere else (external audit 3 #2: the rename was
            sized by Python characters, so one emoji made the name end in ``.jso``)."""
            folder = root / "Chats \U0001F600\U0001F3A8 \U0001D11E\U00020000"
            folder.mkdir()
            store = worker_journal_owner.OwnedSessionStore(str(folder), str(uuid.uuid4()))
            state = store.load()
            for text in ("a", "b"):
                state["messages"].append({"role": "user", "text": text})
                store.save(state)
            reader = session_journal.open_shared(store.path)   # A mirror read in progress: the POSIX rename.
            try:
                store.compact(state)
            finally:
                os.close(reader)
            path = win32_replace.last
            target, source = folder / "held \U0001F600.json", folder / "held \U0001F600.json.new.tmp"
            target.write_bytes(b"old\n")
            source.write_bytes(b"new\n")
            session_journal._replace(source, target, attempts=1)
            if win32_replace.last != win32_replace.POSIX:
                raise AssertionError("the replace ran %s: POSIX semantics are unsupported here" % win32_replace.last)
            if target.read_bytes() != b"new\n" or source.exists():
                raise AssertionError("the replace left %r and its source %s" % (target.read_bytes(), source.exists()))
            chats = Path(store.path).parent   # <folder>\\sessions: the chat's file is <its id>.json, alone there.
            names = sorted(entry.name for entry in chats.iterdir())
            if not str(chats).startswith(str(folder)) or names != [Path(store.path).name]:
                raise AssertionError("the chat is not at its own name: %s in %s" % (names, chats))
            reloaded = SessionStore(str(folder), store.session_id).load()   # Read back as the lead's mirror does.
            if [message["text"] for message in reloaded["messages"]] != ["a", "b"]:
                raise AssertionError("the compacted chat read back as %s" % reloaded["messages"])
            return {"compaction_path": path, "files": names}

        check("path_stat_versus_descriptor", identities)
        check("movefileex_rule", lambda: model("rule"))
        check("lead_store_rewrites", store_rewrites(SessionStore))
        check("owned_store_rewrites", store_rewrites(worker_journal_owner.OwnedSessionStore))
        check("replace_while_a_reader_holds_the_file", replace_while_read)
        check("open_reader_and_compaction", reader_defers)
        check("emoji_folder_saves_at_its_own_name", emoji_folder)

    failed = [name for name, result in results.items() if not result["ok"]]
    print(json.dumps({"passed": not failed, "failed": failed, "replace_path": win32_replace.last,
                      "results": results}, indent=2, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
