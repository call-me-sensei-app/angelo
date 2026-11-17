# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent scripts that attack the confinement, one per class (test_native_script_confinement, run by
confinement_attacks). ``REFUSED``: each must be refused; ``ALLOWED``: ordinary work that must still run; ``PER_PATH``:
run against every private path."""

ATTACKS = {
    # Aliases of a key file: a link made before the script, a link to its folder, case, Unicode, "..".
    "alias_symlink": "open(PLAIN + '/alias.json').read()",
    "alias_folder_link": "open(PLAIN + '/folder-link/' + KEY_NAME).read()",
    "alias_case": "import sys\nif sys.platform in ('darwin', 'win32'): open(ALIAS_CASE).read()\nelse: open(KEY).read()",
    "alias_unicode": "open(ALIAS_NFD).read()",
    "alias_dotdot": "import os\nopen(os.path.join(os.path.dirname(KEY), 'nothing', '..', KEY_NAME)).read()",
    "alias_dir_fd": "import os\nos.read(os.open(KEY_NAME, os.O_RDONLY, dir_fd=HELD_FOLDER), 10)",
    "alias_dev_fd": "import sys\nopen(('/dev/fd/%d' if sys.platform == 'darwin' else '/proc/self/fd/%d') % HELD_FILE).read()",
    "alias_fdopen": "import os\nos.fdopen(os.dup(HELD_FILE)).read()",
    "alias_new_symlink": "import os\nos.symlink(KEY, PLAIN + '/made-link')",
    "alias_new_folder_link": "import os\nos.symlink(os.path.dirname(os.path.dirname(KEY)), PLAIN + '/made-folder-link')",
    "alias_hard_link": "import os\nos.link(KEY, PLAIN + '/hard-link')",
    "chdir_relative": "import os\nos.chdir(os.path.dirname(KEY))\nopen(KEY_NAME).read()",
    "pathlib_read": "from pathlib import Path\nPath(KEY).read_text()",
    "sqlite_open": "import sqlite3\nsqlite3.connect(KEY)",
    "mmap_held": "import mmap\nmmap.mmap(HELD_FILE, 0, access=mmap.ACCESS_READ)",
    "glob_private": "import glob, os\nglob.glob(os.path.dirname(KEY) + '/*')",
    "startup_write": "open(CONFIG + '/scripts/startup/evil.py', 'w').write('import os')",
    "workspace_profile": "open(WORKSPACE + '/claude-profile/auth.json').read()",
    "workspace_beside": "import os\nopen(os.path.dirname(WORKSPACE) + '/session.json').read()",
    "workspace_up": "open(WORKSPACE + '/../session.json').read()",
    "workspace_link_out": "import os\nos.symlink(KEY, WORKSPACE + '/key-link')",
    "tool_output_write": "open(DOWNLOADS + '/chair.txt', 'w').write('changed')",
    "tool_output_remove": "import os\nos.remove(DOWNLOADS + '/chair.txt')",
    "tool_output_rename_in": ("import os\nopen(PLAIN + '/new.txt', 'w').write('x')\n"
                              "os.replace(PLAIN + '/new.txt', DOWNLOADS + '/new.txt')"),
    "startup_rename_in": ("import os\nopen(PLAIN + '/evil.py', 'w').write('x')\n"
                          "os.replace(PLAIN + '/evil.py', CONFIG + '/scripts/startup/evil.py')"),
    # Programs.
    "subprocess_run": "import subprocess\nsubprocess.run(['/usr/bin/touch', MARKER])",
    "os_system": "import os\nos.system('/usr/bin/touch ' + MARKER)",
    "posix_spawn": "import os\nos.posix_spawn('/usr/bin/touch', ['touch', MARKER], dict(os.environ))",
    "fork": "import os\npid = os.fork()\nif pid == 0: os._exit(0)",
    "exec_program": "import os\nos.execv('/nonexistent/program', ['program'])",
    "fork_exec_direct": "import _posixsubprocess\n_posixsubprocess.fork_exec()",
    "multiprocessing": ("import multiprocessing\n"
                        "multiprocessing.get_context('spawn').Process(target=print).start()"),
    # The network (maintainer, 2026-11-15: "Deny network to scripts"): nothing Angelo holds may leave the computer.
    "network_connect": "import socket\nsocket.create_connection(('127.0.0.1', 9), timeout=0.2)",
    "network_resolve": "import socket\nsocket.getaddrinfo('localhost', 80)",
    "network_urllib": "import urllib.request\nurllib.request.urlopen('http://127.0.0.1:9/', timeout=0.2)",
    "network_http": "import http.client\nhttp.client.HTTPConnection('127.0.0.1', 9, timeout=0.2).request('GET', '/')",
    # Native code.
    "ctypes_load": "import ctypes, os\nctypes.CDLL('kernel32' if os.name == 'nt' else None)",
    "ctypes_loaded_call": "LIBC.getpid()",
    "ctypes_memory": "import ctypes\nctypes.string_at(id(KEY), 8)",
    "extension_outside": ("import importlib.util\nspec = importlib.util.spec_from_file_location('_bisect', EXTENSION)\n"
                          "importlib.util.module_from_spec(spec)"),
    # Taking the guard off.
    # CPython drops a new hook silently when an existing one refuses it: the script sees no error, and its hook never
    # runs (the test checks that).
    "unhook_add": ("import sys\nseen = []\nsys.addaudithook(lambda event, args: seen.append(event))\n"
                   "open(PLAIN + '/after-hook.txt', 'w').close()\nassert not seen, 'a script added an audit hook'"),
    "unhook_gc": "import gc\ngc.get_objects()",
    "unhook_trace": "import sys\nsys.settrace(None)",
    "unhook_frames": "import sys\nsys._current_frames()",
    # The script's own function given installed code (it would no longer be the agent's), and code renamed to an
    # installed file (agent code renamed to an agent name stays the agent's, and is allowed).
    "unhook_code_swap": "import shutil\ndef f():\n    pass\nf.__code__ = shutil.rmtree.__code__",
    "unhook_new_function": "import types\ntypes.FunctionType((lambda: 0).__code__, {})",
    "unhook_code_replace": "import os\n(lambda: 0).__code__.replace(co_filename=os.__file__)",
    "unhook_rebind_then_read": (
        "import builtins, os, os.path\nimport sys; c = sys.modules['angelo_agent.script_confinement']\n"
        "c.MARK = 'x'; c.FILE_EVENTS.clear(); c.PROCESS_EVENTS = frozenset()\n"
        "os.path.realpath = lambda p: '/tmp/x'; os.lstat = None\n"
        "builtins.isinstance = lambda *a: False; builtins.any = lambda *a: False; builtins.getattr = None\n"
        "open(KEY).read()"),
    "unhook_counter_then_grant": (
        "import sys; g = sys.modules['angelo_agent.grant_guard']\ng._executing[0] = -100\n"
        "assert g.script_running() and g.inside_agent_code(), 'the grant guard lost the signal'\n"
        "open(KEY).read()"),
    "end_early": "import sys; c = sys.modules['angelo_agent.script_confinement']\nc.end([])\nopen(KEY).read()",
    "add_roots": ("import sys; c = sys.modules['angelo_agent.script_confinement']\n"
                  "add = c._guard.add_roots if c._guard is not None else c._hook['add_roots']\nadd([], [], [], [])"),
}

# Work the script leaves to run later: each must run confined (checked by the parent through the fired results).
LATER = {
    "timer_builtin": "import functools, os\nTIMERS.register(functools.partial(os.system, '/usr/bin/touch ' + MARKER))",
    "timer_function": ("def later():\n    import sys; g = sys.modules['angelo_agent.grant_guard']\n"
                       "    assert g.inside_agent_code(), 'a timer it left is not agent code'\n"
                       "    open(KEY, 'w').write('stolen')\nTIMERS.register(later)"),
    "handler_builtin": ("import functools, os\n"
                        "HANDLERS.depsgraph_update_post.append(functools.partial(os.remove, KEY))"),
    "exec_laundered": "space = {'KEY': KEY}\nexec('def g():\\n    return open(KEY).read()', space)\nLATER.append(space['g'])",
    "compile_named": ("space = {'KEY': KEY}\nexec(compile('def g():\\n    return open(KEY).read()', "
                      "'/Applications/Installed.py', 'exec'), space)\nLATER.append(space['g'])"),
    "thread_reads": ("import threading\ndef work():\n    try:\n        open(KEY).read(); THREADS.append('read')\n"
                     "    except PermissionError:\n        THREADS.append('refused')\n"
                     "t = threading.Thread(target=work); t.start(); t.join()"),
    "thread_raw": ("import _thread, time\ndef work():\n    try:\n        open(KEY).read(); THREADS.append('read')\n"
                   "    except PermissionError:\n        THREADS.append('refused')\n"
                   "_thread.start_new_thread(work, ()); time.sleep(0.3)"),
    "monkeypatch_left": "import sys; r = sys.modules['angelo_agent.private_roots']\nr.registered = lambda: []",
    "path_left": "import sys\nsys.path.insert(0, PLAIN)",
    # Rebinding through the namespace itself, not attribute assignment (the C journal sees the dict change).
    "rebind_dict_item": "import sys; r = sys.modules['angelo_agent.private_roots']\nvars(r)['registered'] = lambda: []",
    "rebind_dict_update": "import sys; r = sys.modules['angelo_agent.private_roots']\nvars(r).update(families=lambda: [])",
    "rebind_exec_globals": "import sys; r = sys.modules['angelo_agent.private_roots']\nexec('def runtime_homes(environ=None):\\n    return []', vars(r))",
    "rebind_object_setattr": "import sys; r = sys.modules['angelo_agent.private_roots']\nobject.__setattr__(r, 'allocate', print)",
    "rebind_clear": "import sys; c = sys.modules['angelo_agent.private_conventions']\nvars(c).clear()",
    "rebind_swap_module": ("import sys, types\nfake = types.ModuleType('angelo_agent.private_roots')\n"
                           "fake.registered = lambda: []\nsys.modules['angelo_agent.private_roots'] = fake"),
    "builtin_left": "import builtins\nbuiltins.open = lambda *a, **k: None",
    # Modules first imported by the script (confinement_attacks drops them from sys.modules first): a rebinding made
    # in one afterwards, and a planted value one copies into its own namespace as it is imported.
    "rebind_fresh_module": "import uuid\nuuid.uuid4 = lambda: 'planted'",
    "rebind_fresh_capture": ("import hmac\nhmac.compare_digest = lambda a, b: True\nimport secrets"),
    "rebind_fresh_import": ("import sys, types\nfake = types.ModuleType('shutil')\nfake.which = lambda *a, **k: 'x'\n"
                            "sys.modules['shutil'] = fake\nimport webbrowser"),
    # The journal's watch list cannot be changed by a script (no watcher can be removed without native code).
    "rewatch": "import _bpy\n_bpy._angelo_guard.watch_modules(['angelo_agent'])",
}

# Ordinary script work that must still run.
ALLOWED = {
    "plain_files": ("import os, shutil, tempfile, json, glob\nos.makedirs(PLAIN + '/sub', exist_ok=True)\n"
                    "open(PLAIN + '/sub/a.json', 'w').write(json.dumps({'a': 1}))\nglob.glob(PLAIN + '/sub/*')\n"
                    "with tempfile.NamedTemporaryFile() as handle: handle.write(b'x')\n"
                    "shutil.rmtree(PLAIN + '/sub')"),
    "read_config": "import os\nos.listdir(CONFIG)\nopen(CONFIG + '/userpref.blend', 'rb').read()",
    "workspace_files": ("import os, json\njson.loads(open(WORKSPACE + '/notes.json').read())\n"
                        "open(WORKSPACE + '/out.json', 'w').write('{}')\nos.makedirs(WORKSPACE + '/sub', exist_ok=True)\n"
                        "os.listdir(WORKSPACE)\nos.remove(WORKSPACE + '/out.json')\nos.rmdir(WORKSPACE + '/sub')"),
    "read_tool_output": ("import os, sys\nos.listdir(DOWNLOADS)\nopen(DOWNLOADS + '/chair.txt').read()\n"
                         "sys.path.insert(0, DOWNLOADS)\nimport download_helper\nKEPT['download_helper'] = download_helper"),
    "python_features": ("import dataclasses, collections, logging, traceback, json, math\n"
                        "@dataclasses.dataclass\nclass P:\n    x: int = 1\nP()\n"
                        "collections.namedtuple('T', 'a b')(1, 2)\nlogging.getLogger('x').warning('hi')\n"
                        "try:\n    1 / 0\nexcept ZeroDivisionError:\n    traceback.format_exc()"),
    "worker_thread": ("import threading\nout = []\nt = threading.Thread(target=lambda: out.append("
                      "open(PLAIN + '/t.txt', 'w').write('x')))\nt.start(); t.join()\nassert out"),
    "trusted_extension": "import lzma",
    "sqlite_plain": "import sqlite3\nsqlite3.connect(PLAIN + '/db.sqlite').close()",
}

PER_PATH = {
    "read": "open(TARGET).read()",
    "write": "open(TARGET, 'w').write('stolen')",
    "os_open": "import os\nos.open(TARGET, os.O_RDONLY)",
    "delete": "import os\nos.remove(TARGET)",
    "rename_out": "import os\nos.rename(TARGET, PLAIN + '/moved')",
    "replace_in": "import os\nopen(PLAIN + '/new', 'w').write('{}')\nos.replace(PLAIN + '/new', TARGET)",
    "chmod": "import os\nos.chmod(TARGET, 0o777)",
    "truncate": "import os\nos.truncate(TARGET, 0)",
    "copy_out": "import shutil\nshutil.copyfile(TARGET, PLAIN + '/copy')",
    "list_folder": "import os\nos.listdir(os.path.dirname(TARGET))",
    "remove_folder": "import os, shutil\nshutil.rmtree(os.path.dirname(TARGET))",
    "new_file_beside": "import os\nopen(os.path.join(os.path.dirname(TARGET), 'new.json'), 'w').write('{}')",
}

# What each attack needs that a platform may lack (confinement_attacks' UNAVAILABLE): not run where missing.
NEEDS = {
    "alias_symlink": "symlink", "alias_folder_link": "symlink", "alias_dir_fd": "dir_fd", "alias_dev_fd": "dev_fd",
    "alias_fdopen": "held_file", "mmap_held": "held_file", "fork": "fork", "posix_spawn": "posix_spawn",
    "fork_exec_direct": "posixsubprocess", "ctypes_loaded_call": "libc", "extension_outside": "extension",
    "alias_unicode": "unicode_alias",
    "rewatch": "journal", "workspace_link_out": "symlink",
}
JOURNAL = ("rebind_fresh_module", "rebind_fresh_capture", "rebind_fresh_import")   # The C journal's (not the stand-in's).

ATTACKS.update(LATER)
ATTACKS.update(ALLOWED)
