# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every replace in the add-on goes through ``shared_replace``, and every reader of a replaced file shares delete.

Windows refuses ``os.replace`` onto a name any handle holds open (``[WinError 5]``): a job's state saved while the
library read it stayed "generating" for good (CI 41887060350). ``test_native_shared_replace`` proves the helper and
``test_native_shared_replace_callers`` the riskiest callers' failure paths; this proves where the add-on calls it.

* ``BARE``: the replaces and renames that do not go through the helper, each with its reason. Any other
  ``os.replace``, ``os.rename``, ``os.renames``, ``shutil.move``, ``Path.replace``, ``Path.rename`` or direct
  ``win32_replace.replace`` in the add-on's Python, a child script's text included, fails here.
* ``READERS``: the functions that read a file the add-on replaces. Each reads through ``shared_replace`` (or a
  reader that does: ``kept_state.read``, ``read_regular``, ``read_key``), never a plain ``open`` or
  ``Path.read_text``/``read_bytes``, so a reader of ours never refuses a writer's replace.
* ``PLAIN``: in every module that replaces a file (``shared_replace.replace``/``retried``, ``atomic_json``,
  ``write_if_unchanged``, ``session_journal.rewrite``), each function that opens a file plainly is a listed reader or
  is listed here with why its file is never replaced while it reads (or why the open writes). A new plain read
  beside a replace fails until it is one or the other. A reader in a module that replaces nothing is out of its
  sight: the replace's own retries are what cover it.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import hashlib
import re
import sys
import tempfile
import tokenize
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

# (file, call as written, without its comment) -> why it stays as it is. The count of each is pinned.
BARE = {
    ("fab_browser_profile.py", "os.replace(name,self.directory/BINDING)"):
        (1, "Darwin-only profile metadata under its exclusive transaction; module requires fcntl and platform "
            "dispatch selects it only on Darwin. Exact source/platform guards are pinned below."),
    ("fab_browser_diagnostics.py", "os.replace(temporary, folder / NAME)"):
        (1, "Private observation written only by Darwin BrowserProcess after its platform admission; Windows "
            "uses its separate native owner. Exact producer/caller guards are pinned below."),
    ("job_output_links.py", "os.replace(name, new_name, src_dir_fd=source, dst_dir_fd=target)"):
        (1, "POSIX descriptor-relative fallback inside held folders, after an exclusive placeholder; Windows uses "
            "WindowsDirectory.move_into. The shared path helper cannot preserve these directory handles. The "
            "documented fallback residual is a replacement of that placeholder just before this rename."),
    ("shared_replace.py", "os.replace(source, target)"):
        (2, "The helper itself: POSIX (no reader refuses a replace there), and a folder win32_replace cannot open."),
    ("shared_replace.py", "win32_replace.replace(source, target)"):
        (1, "The helper's own Windows rename (POSIX semantics), under its retries."),
    ("win32_replace.py", "os.replace(source, target)"):
        (1, "Where POSIX semantics are unsupported (FAT, exFAT, some shares): shared_replace retries this one."),
    ("checkpoint_filesystem.py", "os.replace(source, destination, src_dir_fd=folder, dst_dir_fd=folder)"):
        (1, "POSIX only (an anchored folder descriptor): no reader refuses a replace there; Windows takes "
            "WindowsDirectory.replace under shared_replace.retried."),
    ("checkpoint_filesystem.py", "os.replace"):
        (1, "A capability check (``os.replace in os.supports_dir_fd``): no move."),
    ("checkpoint_filesystem.py", "os.rename"):
        (1, "A capability check (``os.rename in os.supports_dir_fd``): no move."),
    ("folder_moves.py", "os.rename(source, target)"):
        (1, "Never over an existing name by design (a migration's move); a refusal resumes at the next launch."),
    ("studio_files.py", "os.rename(temporary, path)"):
        (1, "Create-only on Windows (refuses an existing name by design); write_if_unchanged replaces."),
    ("team_library_host.py", "target.rename(final)"):
        (1, "A fresh name in the job's own new private folder: nothing is there to replace, nothing reads it."),
    ("unreal_plugin/AngeloLink/Content/Python/angelo_link/live.py", "os.replace(path + \".part\", path)"):
        (1, "Runs inside Unreal (no add-on modules there); its only reader is the same thread, after it."),
    ("worker_crash_evidence.py", "shutil.move(str(log), str(target))"):
        (1, "Across volumes, to a name with the worker's process ID (only a reused ID finds one there); a "
            "failure is said and the crash log goes with its private folder."),
}

# (file, function) -> the file it reads. Each reads through the shared reader.
READERS = {
    ("always_allow.py", "attach"): "always-allow-scripts.json",
    ("cache_affinity_secret.py", "_read"): "cache-affinity-secret",
    ("chat_pending.py", "load"): "a chat's pending messages",
    ("codex_bases.py", "_read"): "codex_bases.json",
    ("codex_bases.py", "_failures"): "codex_bases.json",
    ("engine_link_stamps.py", "read"): "a linked asset's stamp",
    ("engine_link_stamps.py", "written"): "the child's stamp for one save and writer",
    ("engine_link_assets.py", "digest"): "the linked asset (the write-back replaces it)",
    ("engine_link_ui.py", "remembered"): "engine-link-settings.json",
    ("engine_preflight.py", "link_report"): "a linked asset's preflight report",
    ("engine_suggest.py", "__init__"): "engine-suggestions.json",
    ("external_setup.py", "_read_text"): "an app's MCP settings (the setup replaces them)",
    ("external_setup.py", "has_entry"): "Codex's config.toml (the setup replaces it)",
    ("fal_video.py", "load_video"): "a reference video (a result _write replaces may be one)",
    ("headless_files.py", "check_source"): "a .blend (an earlier output place() may replace)",
    ("goal_store.py", "_saved"): "goals.json",
    ("external_cli_protocol.py", "read_key"): "an app's key file",
    ("external_requests.py", "_trim"): "the audit log",
    ("kept_state.py", "read"): "jobs.json, connections.json, conversations.json, cli/clients.json",
    ("library_fetch.py", "read_local"): "the library index",
    ("library_agent.py", "_previews"): "a library thumbnail (another Angelo's fetch may replace it)",
    ("library_fetch.py", "_digests"): "a library item's cached file (another Angelo's fetch may replace it)",
    ("private_roots.py", "retained_profiles"): "the retained native-profile lists",
    ("runtime_launch.py", "codex_command"): "Codex's config.toml (the MCP setup replaces it)",
    ("runtime_threads.py", "_read"): "worker-threads.json",
    ("script_files.py", "_read"): "external-script-files.json",
    ("session_kept.py", "preserve"): "a chat's kept copies (redact_kept rewrites them)",
    ("session_kept.py", "redact_kept"): "a chat's kept copies (rewritten here)",
    ("settings_import.py", "backup_matches"): "the settings import's manifest",
    ("settings_import.py", "_place"): "a merged list it replaces (and the earlier Blender's own)",
    ("studio_assets.py", "unclean_record"): "assets/<id>.json (the library's records)",
    ("studio_files.py", "_read_bytes"): "blender_assets.cats.txt",
    ("studio_files.py", "read_own"): "Studio's own JSON files",
    ("studio_generation.py", "load_references"): "a reference image (a Studio result may be one)",
    ("studio_state.py", "import_image"): "an imported image (a Studio result may be one)",
    ("studio_state.py", "read_json"): "a job's state",
    ("team_org_policy.py", "read_regular"): "team-org-policy.json and the launcher's mcp/ records",
    ("team_refresh_marks.py", "_load"): "team-refresh.json",
    ("team_usage_buffer.py", "_read"): "team-usage.json",
    ("telemetry.py", "_load"): "performance.json",
    ("update_download.py", "_digest_of"): "the downloaded file its replace puts in place",
    ("usage_meter.py", "_saved"): "usage.json",
    ("worker_routing.py", "registry"): "worker-chats.json",
}

_FOLDER_SYNC = "Opens a folder to sync it after a replace: no file is read."
_OWN_NEW = "Creates or appends to its own file (write flags in a variable): nothing is read."
_SHARED_ITSELF = "The shared reader itself (session_journal.open_shared shares delete on Windows)."
# (file, function) -> why its plain open never refuses a replace. Pinned both ways: a stale entry fails too.
PLAIN = {
    ("always_allow.py", "_write"): _FOLDER_SYNC,
    ("checkpoint_filesystem.py", "_open_file"):
        "Snapshot files: on Windows a WindowsDirectory opens them sharing read, write and delete (windows_nt "
        "FILE_SHARE_ALL); the other branches are POSIX, where no reader refuses a replace.",
    ("engine_link_stamps.py", "write"): _OWN_NEW,
    ("engine_preflight.py", "write_report"): _OWN_NEW,
    ("engine_preflight.py", "after_writeback"):
        "In the preflight child: a copy of the asset made for that child alone; nothing replaces it.",
    ("external_requests.py", "record"): _OWN_NEW,
    ("headless_files.py", "claim_output"): _OWN_NEW,
    ("headless_files.py", "place"): _OWN_NEW,
    ("script_files.py", "read_script"): "The person's own script file, which Angelo never writes.",
    ("script_files.py", "_write"): _FOLDER_SYNC,
    ("session_journal.py", "open_shared"): _SHARED_ITSELF,
    ("session_kept.py", "_sync_folder"): _FOLDER_SYNC,
    ("state_file_writes.py", "write_receipts"): _OWN_NEW,
    ("studio_assets_convert.py", "_save"): _FOLDER_SYNC,
    ("studio_assets_convert.py", "main"):
        "The batch file its parent wrote before it started this child; nothing replaces it.",
    ("studio_files.py", "_create"): "Its own temporary file, written a moment before under a fresh name.",
    ("team_refresh_marks.py", "_save"): _FOLDER_SYNC,
    ("team_usage_buffer.py", "_write"): _OWN_NEW,
    ("update_download.py", "_read_meta"): "A partial download's notes, written in place (never replaced).",
    ("update_download.py", "_copy"): "Writes the partial download (its mode is a variable): nothing is read.",
    ("update_stage_mac.py", "info"): "Info.plist inside a staged app, read after staging; never replaced.",
    ("web_export.py", "_write"): "The file gltfpack just wrote, read and rewritten by this function alone.",
}

_TEXT = re.compile(r"\b(?:os\.(?:replace|rename|renames)|shutil\.move|win32_replace\.replace)\(")
_MOVES = {("os", "replace"), ("os", "rename"), ("os", "renames"), ("shutil", "move"), ("win32_replace", "replace")}
_SHARED = re.compile(r"\b(?:shared_replace\.(?:open_shared|open_file|read_bytes|read_text)|kept_state\.read"
                     r"|read_regular|session_journal\.(?:open_shared|read_bytes))\(")
_PLAIN = re.compile(r"(?<![\w.])open\((?!shared_replace\.open_shared)|\bos\.open\(|\.open\("
                    r"|(?<!shared_replace)(?<!session_journal)\.read_(?:text|bytes)\(|\bshutil\.copy(?:file|2)?\(")
_WRITER = re.compile(r"\b(?:shared_replace\.(?:replace|retried)|atomic_json|write_if_unchanged"
                     r"|session_journal\.rewrite)\(")
_WRITE_FLAGS = re.compile(r"O_(?:WRONLY|RDWR|CREAT|APPEND|DIRECTORY|TRUNC)\b")
# A file's name (a stem and an extension) in a module that replaces files: a reader elsewhere that names it is checked.
_FILE_NAME = re.compile(r"^[\w-][\w.-]*\.(?:json|jsonl|toml|txt|blend|glb|gltf|fbx|png|jpg|mp4|plist)$")
_SHARED_OWNERS = {"shared_replace", "session_journal"}
# Another name for a replace or rename the scan would not see.
_ALIAS = re.compile(r"^\s*(?:from\s+os\s+import\s+.*\b(?:replace|rename|renames)\b|from\s+shutil\s+import\s+.*\bmove\b"
                    r"|import\s+(?:os|shutil)\s+as\b)[^\n]*", re.M)


def _code(line):
    """A line's code without its comment (a ``#`` inside a string literal is kept)."""
    try:
        tokens = list(tokenize.generate_tokens(iter([line + "\n"]).__next__))
    except (tokenize.TokenError, SyntaxError):   # A line that is no whole statement: cut at the first "  #".
        return line.split("  #")[0].strip()
    comment = next((token.start[1] for token in tokens if token.type == tokenize.COMMENT), None)
    return (line[:comment] if comment is not None else line).strip()


def found_sites(root=ADDON):
    """Counter of (file, call) for every replace or rename in the Python under ``root`` (the add-on)."""
    found = Counter()
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        for match in _ALIAS.finditer(source):
            found[(relative, match.group(0).strip())] += 1
        for line in source.splitlines():   # Text: a child script's string counts too.
            for match in _TEXT.finditer(line):
                code = _code(line)
                start = code.find(match.group(0))
                found[(relative, _call(code[start:] if start >= 0 else code))] += 1
        tree = ast.parse(source)
        called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        for node in ast.walk(tree):   # Path.replace(target) / Path.rename(target): one argument.
            if _path_move(node):
                found[(relative, ast.get_source_segment(source, node))] += 1
            elif (isinstance(node, ast.Attribute) and id(node) not in called and isinstance(node.value, ast.Name)
                  and (node.value.id, node.attr) in _MOVES):   # Passed as a value: ``retried(os.replace, ...)``.
                found[(relative, ast.unparse(node))] += 1
    return found


def _reads(call):
    """Whether ``call`` opens a file plainly to read it: ``open``/``*.open`` without a writing mode, ``os.open``
    without writing flags, ``*.read_text``/``*.read_bytes``, ``shutil.copy*`` (it reads its source). The shared
    readers (``shared_replace.*``, ``session_journal.*``, ``open`` of their descriptor) are not plain."""
    func = call.func
    owner = ast.unparse(func.value) if isinstance(func, ast.Attribute) else ""
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if owner in _SHARED_OWNERS or owner == "webbrowser":
        return False
    if owner == "shutil":
        return name in ("copy", "copy2", "copyfile")
    if name in ("read_text", "read_bytes"):
        return True
    if owner == "os":
        return name == "open" and not _WRITE_FLAGS.search(ast.unparse(call))
    if name != "open" or (call.args and isinstance(call.args[0], ast.Call)
                          and ast.unparse(call.args[0].func).split(".")[-1] == "open_shared"):
        return False
    mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return not set(mode.value) & set("wax+")
    return mode is None or not _WRITE_FLAGS.search(ast.unparse(mode))


def _functions(tree):
    """(function, the calls in its own body: not a nested function's or class's)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls, todo = [], list(ast.iter_child_nodes(node))
            while todo:
                child = todo.pop()
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    calls += [child] if isinstance(child, ast.Call) else []
                    todo.extend(ast.iter_child_nodes(child))
            yield node, calls


def plain_reads(root=ADDON):
    """{(file, function)} for each function that opens a file plainly for reading (``_reads``), in every module
    under ``root`` that replaces a file, and in any other module where the function names a file such a module
    names (a literal, or a module constant holding one)."""
    sources = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.py"))}
    trees = {relative: ast.parse(source) for relative, source in sources.items()}
    writers = {relative for relative, source in sources.items() if _WRITER.search(source)}
    names, constants = set(), set()
    for relative in writers:
        for node in ast.walk(trees[relative]):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _FILE_NAME.match(node.value):
                names.add(node.value)
        for node in trees[relative].body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str) and _FILE_NAME.match(node.value.value)):
                constants.update(target.id for target in node.targets if isinstance(target, ast.Name))
    found = set()
    for relative, tree in trees.items():
        for function, calls in _functions(tree):
            if not any(_reads(call) for call in calls):
                continue
            if relative not in writers and not any(
                    (isinstance(node, ast.Constant) and node.value in names)
                    or (isinstance(node, ast.Name) and node.id in constants)
                    or (isinstance(node, ast.Attribute) and node.attr in constants) for node in ast.walk(function)):
                continue
            found.add((relative, function.name))
    return found


def _path_move(node):
    """``Path.replace``/``Path.rename``: one argument, positional (never a constant: ``str.replace`` has two) or
    ``target=``; ``os.*`` and ``dataclasses.replace`` are not it."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("replace", "rename")):
        return False
    if ast.unparse(node.func.value) in ("os", "shutil", "dataclasses", "win32_replace"):
        return False
    positional = len(node.args) == 1 and not node.keywords and not isinstance(node.args[0], ast.Constant)
    return positional or (not node.args and [keyword.arg for keyword in node.keywords] == ["target"])


def _call(text):
    """The call at the start of ``text``, up to its closing parenthesis."""
    depth = 0
    for index, character in enumerate(text):
        depth += character == "("
        depth -= character == ")"
        if character == ")" and depth == 0:
            return text[:index + 1]
    return text


def function_source(relative, name):
    source = (ADDON / relative).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError("%s has no function %s" % (relative, name))


class SitesTests(unittest.TestCase):
    def test_every_replace_goes_through_the_helper_or_is_listed_with_its_reason(self):
        found = found_sites()
        expected = Counter({site: count for site, (count, _reason) in BARE.items()})
        unlisted = {site: count for site, count in found.items() if site not in BARE}
        self.assertEqual(unlisted, {}, "A replace or rename outside shared_replace: route it through "
                                       "shared_replace.replace, or list it in BARE with why it stays bare")
        self.assertEqual(Counter({site: found[site] for site in BARE}), expected,
                         "A listed bare site moved or changed: update BARE (and its reason) with it")

    def test_fab_posix_bare_sites_keep_exact_platform_and_producer_guards(self):
        pins = {('fab_browser_profile.py', 'ProfileStore._write'): 'fd83eb94b165aaf60c36b4bb50acd26ac49608e083b5b7852a83a8e09be1ac01', ('fab_browser_diagnostics.py', 'write_record'): '736ae286010ff627064cc43df5652da97252d1efa59cc6242a56d6f15ce33160', ('fab_browser_process.py', 'BrowserProcess.__init__'): '6405fb770bfb105b8f46e7dbd69661209e56c436ba01fa647872bcbd7e3f315a', ('fab_browser_process.py', 'BrowserProcess._record'): '204bdf8a98398184aa0bd1d934a8383d14112dec2b15a6b767d35edb10cecaf5', ('fab_browser_platform.py', 'profile_types'): '2f6f951de8fe60a2bf05e42fa088ad33b89861a61b522046a4eb24fcea41d277'}
        for (module, function), digest in pins.items():
            with self.subTest(module=module, function=function):
                node = ast.parse((ADDON / module).read_text())
                for component in function.split('.'):
                    found = [child for child in node.body
                             if isinstance(child, (ast.FunctionDef, ast.ClassDef)) and child.name == component]
                    self.assertEqual(len(found), 1)
                    node = found[0]
                self.assertEqual(hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest(), digest)
        profile = ast.parse((ADDON / 'fab_browser_profile.py').read_text())
        self.assertTrue(any(isinstance(node, ast.Import) and any(alias.name == 'fcntl' for alias in node.names)
                            for node in profile.body))
        consumers = {path.name for path in ADDON.rglob('*.py')
                     if 'fab_browser_diagnostics' in path.read_text()}
        self.assertEqual(consumers, {'fab_browser_process.py'})
        own = {site: count for site, count in found_sites().items()
               if site[0] in {'fab_browser_diagnostics.py', 'fab_browser_profile.py', 'fab_cache.py'}}
        expected = {site: count for site, (count, _) in BARE.items()
                    if site[0] in {'fab_browser_diagnostics.py', 'fab_browser_profile.py'}}
        self.assertEqual(own, expected)

    def test_the_checker_finds_each_kind_of_bare_replace(self):
        """Held out: a source with one of each kind the checker must flag (failing first on a scan that missed any)."""
        source = ("import os, shutil\nfrom pathlib import Path\n"
                  "os.replace(a, b)\nos.rename(a, b)\nshutil.move(a, b)\nPath(a).replace(b)\nPath(a).rename(b)\n"
                  "win32_replace.replace(a, b)\nCHILD = '''\nos.replace(scratch, target)\n'''\n"
                  "text.replace('a', 'b')\ndataclasses.replace(record)\nPath(a).replace(target=b)\n"
                  "from os import replace as move_into_place\nshared_replace.retried(os.replace, a, b)\n")
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "probe.py").write_text(source, encoding="utf-8")
            found = found_sites(Path(folder))
        self.assertEqual(sorted(call for _file, call in found), sorted([
            "os.replace(a, b)", "os.rename(a, b)", "shutil.move(a, b)", "Path(a).replace(b)", "Path(a).rename(b)",
            "win32_replace.replace(a, b)", "os.replace(scratch, target)", "Path(a).replace(target=b)",
            "from os import replace as move_into_place", "os.replace"]))

    def test_the_reader_check_refuses_each_plain_open(self):
        """Held out: each way to open a file plainly fails the reader check; the shared ways pass."""
        for plain in ('open(path, "rb")', "Path(path).read_text()", "path.read_bytes()", "os.open(path, flags)",
                      "path.open()", "io.open(path)", "gzip.open(path)"):
            with self.subTest(plain=plain):
                self.assertTrue(_PLAIN.search(plain))
        for shared in ("shared_replace.read_text(path)", "shared_replace.read_bytes(path)",
                       "open(shared_replace.open_shared(path), 'rb')", "shared_replace.open_file(path)",
                       "os.fdopen(fd)", "session_journal.read_bytes(path)"):
            with self.subTest(shared=shared):
                self.assertIsNone(_PLAIN.search(shared))
                self.assertRegex(shared, r"shared_replace|session_journal|fdopen")

    def test_every_listed_reader_shares_delete(self):
        for (relative, name), what in READERS.items():
            with self.subTest(reader="%s:%s" % (relative, name), file=what):
                code = "\n".join(_code(line) for line in function_source(relative, name).splitlines())
                self.assertRegex(code, _SHARED, "This reader of a replaced file must read through shared_replace")
                plain = [match.group(0) for match in _PLAIN.finditer(code)]
                self.assertEqual(plain, [], "A plain open in a reader of a replaced file refuses its writer's "
                                            "replace on Windows")

    def test_every_plain_open_beside_a_replace_is_a_listed_reader_or_has_its_reason(self):
        found = plain_reads()
        self.assertEqual(sorted(found - set(READERS) - set(PLAIN)), [],
                         "A plain open in a module that replaces files: read through shared_replace (and list it "
                         "in READERS), or list it in PLAIN with why its file is never replaced while it reads")
        self.assertEqual(sorted(set(PLAIN) - found), [], "A PLAIN entry no longer opens plainly: remove it")

    def test_the_plain_read_census_finds_a_reader_beside_a_replace(self):
        """Held out: a plain read in a module that replaces is found; one in a module that replaces nothing, a write,
        a nested function's read (its own entry) and a shared read are not counted against the outer function."""
        writer = ("STATE = 'state.json'\n"
                  "def save(p):\n    shared_replace.replace(t, p)\n"
                  "def load(p):\n    return json.loads(p.read_text())\n"
                  "def write(p):\n    with open(p, 'wb') as h:\n        h.write(b'')\n"
                  "def both(a, b):\n    with open(a, 'rb') as s, open(b, 'w') as d:\n        d.write(s.read())\n"
                  "def copy(a, b):\n    shutil.copy2(a, b)\n"
                  "def shared(p):\n    return shared_replace.read_text(p), open(shared_replace.open_shared(p))\n"
                  "def outer(p):\n    def inner():\n        return open(p, 'rb')\n    return inner\n")
        reader = ("def named(f):\n    return (f / 'state.json').read_bytes()\n"
                  "def by_constant(f):\n    return open(f / writer.STATE)\n"
                  "def unrelated(p):\n    return open(p).read()\n")
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "writer.py").write_text(writer, encoding="utf-8")
            (Path(folder) / "reader.py").write_text(reader, encoding="utf-8")
            found = plain_reads(Path(folder))
        self.assertEqual(sorted(found), [("reader.py", "by_constant"), ("reader.py", "named"), ("writer.py", "both"),
                                         ("writer.py", "copy"), ("writer.py", "inner"), ("writer.py", "load")])

    def test_the_shared_readers_share_delete_on_windows(self):
        """``read_regular`` and ``read_key`` (readers of several replaced files) open through the helper."""
        for relative, name in (("team_org_policy.py", "read_regular"), ("external_cli_protocol.py", "read_key"),
                               ("kept_state.py", "read")):
            with self.subTest(reader=name):
                self.assertIn("shared_replace.", function_source(relative, name))


if __name__ == "__main__":
    unittest.main()
