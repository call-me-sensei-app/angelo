# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The escape matrix (test_blender_confinement_escapes; break-it review 2026-11-15, MUST-1..5): a script leaves
code behind to run later, outside its run, and each payload records where it ran.

Every trigger (what runs the code later: a destructor, the executor reading the result, a finalizer, a weak
reference's callback, a garbage-collection callback, a class attribute, a module's new name, a module-level
registry, an attribute of an object Angelo holds) is combined with every carrier (what the code is: a function
compiled under an installed file's name, an existing file's name or a ``<frozen …>`` name, a function whose code
was renamed after compiling, ``partial(exec, …)``, ``staticmethod(partial(exec, …))``, an ``lru_cache`` whose
``__wrapped__`` was forged) that the trigger can call.
Each payload appends ``TAG confined=… read=…`` to ``FLAGS``: whether it ran confined, and whether it could read
Angelo's private key. Nothing may ever write ``confined=False`` or ``read=True``. A carrier the confinement refuses
to make is a pass too (its trigger never fires).
"""

PAYLOAD = """\
import _bpy
_line = {tag!r} + " confined=" + str(_bpy._angelo_guard.confined())
try:
    open({key!r}).read()
    _line += " read=True"
except Exception:
    _line += " read=False"
open({flags!r}, "a").write(_line + "\\n")
"""

# What the code is. ``P`` (the payload's text) is set before each; each defines ``X``.
CARRIERS = {
    "installed_name": ("import os\n_ns = {'P': P}\n"
                       "exec(compile('def X(*a, **k):\\n    exec(P)\\n', os.path.join(os.path.dirname(os.__file__), "
                       "'nothing_here.py'), 'exec'), _ns)\nX = _ns['X']\n"),
    "existing_file_name": ("import os\n_ns = {'P': P}\n"
                           "exec(compile('def X(*a, **k):\\n    exec(P)\\n', os.__file__, 'exec'), _ns)\n"
                           "X = _ns['X']\n"),
    "frozen_name": ("_ns = {'P': P}\n"
                    "exec(compile('def X(*a, **k):\\n    exec(P)\\n', '<frozen breakit>', 'exec'), _ns)\n"
                    "X = _ns['X']\n"),
    "renamed_code": ("import os\ndef X(*a, **k):\n    exec(P)\n"
                     "X.__code__ = X.__code__.replace(co_filename=os.__file__)\n"),
    "partial_exec": "import functools\nX = functools.partial(exec, P)\n",
    "staticmethod_partial": "import functools\nX = staticmethod(functools.partial(exec, P))\n",
    # A C wrapper whose settable ``__wrapped__`` names installed code while it calls the script's own.
    "lru_forged": ("import functools\nX = functools.lru_cache(maxsize=None)(lambda *a, **k: exec(P))\n"
                   "X.__wrapped__ = len\n"),
}
FUNCTIONS = ("installed_name", "existing_file_name", "frozen_name", "renamed_code")   # Take any arguments.

# What runs it later: (the script's part, the carriers it can call, what Angelo does later: ``LATER`` in the runner).
TRIGGERS = {
    "del_namespace": ("class B:\n    __del__ = X\nkeep = B()\n", None, None),
    "del_cycle": ("class B:\n    __del__ = X\nb = B()\nb.me = b\ndel b\n", None, "collect"),
    # A metaclass written in Python (installed) makes the class: still the script's class statement.
    "del_abc": ("import abc\nclass B(abc.ABC):\n    __del__ = X\nkeep = B()\n", None, None),
    "repr_result": ("class R:\n    __repr__ = X\nresult = R()\n", None, None),
    "str_held": ("class S:\n    __str__ = X\nHOLDER.shown = S()\n", None, "str_held"),
    "hash_held": ("class H:\n    __hash__ = X\nHOLDER.hashed = H()\n", None, "hash_held"),
    "finalize_cycle": ("import weakref\nclass N:\n    pass\nn = N()\nn.me = n\nweakref.finalize(n, X)\ndel n\n",
                       None, "collect"),
    "finalize_held": ("import weakref\nclass N:\n    pass\nHOLDER.target = N()\n"
                      "HOLDER.finalizer = weakref.finalize(HOLDER.target, X)\n", None, "finalize_held"),
    "weakref_callback": ("import weakref\nclass N:\n    pass\nHOLDER.target2 = N()\n"
                         "HOLDER.ref = weakref.ref(HOLDER.target2, X)\n", FUNCTIONS, "drop_target2"),
    "gc_callback": ("import gc\ngc.callbacks.append(X)\n", FUNCTIONS, "collect"),
    "class_attribute": ("import sys\nsys.modules['angelo_agent.files'].FileWorkspace.breakit_planted = X\n", None,
                        "class_attribute"),
    "class_method": ("import json\njson.JSONEncoder.default = X\n", FUNCTIONS, "class_method"),
    "module_name": ("import sys\nm = sys.modules['angelo_agent.private_conventions']\nm.breakit_planted = X\n", None,
                    "module_name"),
    "module_registry": ("import sys\nsys.modules['angelo_agent.grant_guard'].ENTRIES['breakit.planted'] = X\n", None,
                        "module_registry"),
    "held_attribute": ("HOLDER.cb = X\n", None, "held_attribute"),
}

# Rebindings that must not outlast the script (MUST-2 class attributes, MUST-5 a definition with a forged name).
REBINDINGS = {
    "class_plain": ("import sys\ncontroller = sys.modules['angelo_agent.controller']\ncontroller.Controller.stop = len\n"
                    "controller.Controller.breakit = 1\nimport json\njson.JSONEncoder.breakit_direct = 1\n"
                    "setattr(json.JSONEncoder, 'breakit_set', len)\nimport dataclasses\n"
                    "dataclasses._set_new_attribute(json.JSONEncoder, 'breakit_helper', len)\n"
                    "import bpy\nbpy.types.Operator.breakit_method = len\n"),
    "class_dict_through_gc": "import gc, json\ngc.get_referents(json.JSONEncoder)[0]['breakit_gc'] = len\n",
    "definition_installed_name": ("import shutil\n"
                                  "exec(compile('which = len', shutil.__file__, 'exec'), shutil.__dict__)\n"),
    "definition_frozen_name": ("import shutil\n"
                               "exec(compile('copyfile = len', '<frozen anything>', 'exec'), shutil.__dict__)\n"),
}

# SQLite (MUST-3): no private file read or written through it, no native code loaded through it.
SQLITE = {
    "sqlite_attach_read": ("import sqlite3\nc = sqlite3.connect(':memory:')\n"
                           "c.execute('ATTACH DATABASE ? AS p', (PRIVATE_DB,))\n"
                           "ROWS.extend(c.execute('SELECT * FROM p.t').fetchall())\n"),
    "sqlite_attach_plant": ("import sqlite3\nc = sqlite3.connect(':memory:')\n"
                            "c.execute('ATTACH DATABASE ? AS p', (STATE + '/planted.db',))\n"
                            "c.execute('CREATE TABLE p.x (a)')\nc.commit()\n"),
    "sqlite_attach_uri": ("import sqlite3\nc = sqlite3.connect(':memory:')\n"
                          "c.execute('ATTACH DATABASE ? AS p', ('file:' + PRIVATE_DB + '?mode=ro',))\n"
                          "ROWS.extend(c.execute('SELECT * FROM p.t').fetchall())\n"),
    "sqlite_vacuum_into": ("import sqlite3\nc = sqlite3.connect(PLAIN + '/a.db')\nc.execute('CREATE TABLE x (a)')\n"
                           "c.commit()\nc.execute('VACUUM INTO ?', (STATE + '/vacuumed.db',))\n"),
    "sqlite_raise_limit": ("import sqlite3\nc = sqlite3.connect(':memory:')\n"
                           "c.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 10)\n"
                           "c.execute('ATTACH DATABASE ? AS p', (PRIVATE_DB,))\n"
                           "ROWS.extend(c.execute('SELECT * FROM p.t').fetchall())\n"),
    "sqlite_load_extension": ("import sqlite3\nc = sqlite3.connect(':memory:')\nc.enable_load_extension(True)\n"
                              "c.load_extension(EXTENSION)\n"),
    "sqlite_vfs": "import sqlite3\nsqlite3.connect('file:' + PLAIN + '/v.db?vfs=unix-none', uri=True)\n",
}

# A text that runs when a file opens (MUST-4), saved by the script.
AUTORUN = {
    "use_module_set": ("t = bpy.data.texts.new('breakit_hook.py')\nt.write('import os')\nt.use_module = True\n"),
    "use_module_saved": ("t = bpy.data.texts.new('breakit_saved.py')\nt.write('import os')\n"
                         "try:\n    t.use_module = True\nexcept Exception:\n    pass\n"
                         "bpy.ops.wm.save_as_mainfile(filepath=PLAIN + '/project.blend', copy=True)\n"),
    "use_module_appended": ("with bpy.data.libraries.load(AUTORUN_BLEND, link=False) as (src, dst):\n"
                            "    dst.texts = ['autorun.py']\n"
                            "bpy.ops.wm.save_as_mainfile(filepath=PLAIN + '/appended.blend', copy=True)\n"),
}

# Descriptors Angelo already holds open (SHOULD-1).
DESCRIPTORS = {
    "fd_read_private": "import os\nos.lseek(HELD_KEY, 0, 0)\nROWS.append(os.read(HELD_KEY, 100))\n",
    "fd_pread_private": "import os\nROWS.append(os.pread(HELD_KEY, 100, 0))\n",
    "fd_write_socket": "import os\nos.write(HELD_SOCKET, b'exfil')\n",
}

# Ordinary code that must still run (SHOULD-2 and compatibility).
ORDINARY = {
    "namedtuple_defaults": ("import collections\nP = collections.namedtuple('P', 'x y', defaults=(0,))\n"
                            "assert P(1).y == 0\n"),
    "asyncio_run": "import asyncio\nasync def f():\n    return 1\nassert asyncio.run(f()) == 1\n",
    "unittest_mock": "import unittest.mock\nm = unittest.mock.MagicMock()\nm.x(1)\n",
    "types_coroutine": "import types\n@types.coroutine\ndef g():\n    yield\nlist(g())\n",
    "sqlite_plain": ("import sqlite3\nc = sqlite3.connect(PLAIN + '/plain.db')\n"
                     "c.execute('CREATE TABLE IF NOT EXISTS t (a)')\nc.execute('INSERT INTO t VALUES (1)')\n"
                     "c.commit()\nassert c.execute('SELECT * FROM t').fetchall()\n"),
    "class_registration": ("import bpy\nclass BREAKIT_OT_ok(bpy.types.Operator):\n    bl_idname = 'breakit.ok'\n"
                           "    bl_label = 'ok'\n    def execute(self, context):\n        return {'FINISHED'}\n"
                           "bpy.utils.register_class(BREAKIT_OT_ok)\nbpy.ops.breakit.ok()\n"
                           "bpy.types.Scene.breakit_prop = bpy.props.IntProperty()\n"),
    "thread_pool": ("import multiprocessing.dummy\nwith multiprocessing.dummy.Pool(2) as pool:\n"
                    "    assert pool.map(abs, [-1, 2]) == [1, 2]\n"),
    # A semaphore under a start method that isn't fork (macOS's default; asked for here on any platform): it must
    # work with no helper process (multiprocessing's resource tracker) started for it.
    "spawn_semaphore": ("import multiprocessing\nfrom multiprocessing.pool import ThreadPool\n"
                        "ctx = multiprocessing.get_context('spawn')\nlock = ctx.Lock()\nwith lock:\n    pass\n"
                        "s = ctx.BoundedSemaphore(2)\ns.acquire()\ns.release()\n"
                        "c = ctx.Condition()\nwith c:\n    c.notify_all()\nctx.Event().set()\n"
                        "with ThreadPool(2) as pool:\n    assert pool.map(abs, [-1, 2]) == [1, 2]\n"),
    "lazy_c_import": "import time\nassert time.strptime('2020', '%Y').tm_year == 2020\n",
    "functions_and_classes": ("import functools\nclass A:\n    def __repr__(self):\n        return 'a'\n"
                              "result = {'a': repr(A()), 'n': functools.reduce(lambda x, y: x + y, [1, 2])}\n"),
}

# Installed packages (written to the profile's user ``scripts/modules``, an installed folder) that define themselves
# the ways real ones do; a confined script imports each first, and they must stay as they defined themselves.
IMPORT_FIXTURES = {
    # USD's ``Tf.PrepareModule``: a helper copies names in, ``__spec__`` included (replacing the package's own), and
    # the package binds more names after it.
    "angelo_fixture_defines/_source.py": '''\
from math import sqrt as native_sqrt   # Native, as USD's re-exported functions are.


def made():
    return 1


def prepare(result):
    for key, value in list(globals().items()):
        if key not in ("__name__", "__builtins__", "__doc__", "__file__", "__path__"):
            result[key] = value
''',
    "angelo_fixture_defines/__init__.py": '''\
from . import _source
_source.prepare(globals())
import functools
import typing


class Cached:
    @functools.lru_cache(maxsize=None)
    def twice(self, x):
        return 2 * x

    @functools.cached_property
    def lazy(self):
        return 1


class Sized(typing.Protocol):   # Its __init_subclass__ sets attributes while type.__new__ runs.
    def size(self) -> int:
        ...


def late():
    return 3
''',
    # An installed package that loads a file the script wrote while it imports (a plugin folder): what that file
    # defines is the script's code wherever the package puts it.
    "angelo_fixture_plugins/__init__.py": '''\
import importlib.util
import os
_where = open(os.path.join(os.path.dirname(__file__), "plugin_path.txt")).read().strip()
_spec = importlib.util.spec_from_file_location("angelo_fixture_plugin_file", _where)
_plugin = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_plugin)
handler = _plugin.handler


class Holder:
    held = _plugin.handler
''',
}
PLUGIN_FILE = ("def handler(*args, **kwargs):\n    import _bpy\n"
               "    return _bpy._angelo_guard.confined()\n")

# Standard-library caches and memos a script fills (coordinator, 2026-11-15): each source runs confined twice, then
# unconfined, and must work every time; afterwards the installed modules hold no confining wrappers. ``HELD`` is a
# list Angelo holds, made before any script.
CACHE_MATRIX = {
    "lru_cache_fnmatch": "import fnmatch\nassert fnmatch.fnmatch('a.txt', '*.txt')\n",
    "re_cache": "import re\nassert re.compile('a+b').match('aab')\nassert re.sub('a', 'b', 'aa') == 'bb'\n",
    "cached_property": ("import ipaddress\nn = ipaddress.ip_network('10.0.0.0/8')\n"
                        "assert str(n.broadcast_address) == '10.255.255.255'\nHELD.append(n)\n"),
    # FastPath caches itself and an lru_cache of its bound ``lookup``; a root that is gone clears that cache.
    "importlib_metadata": ("import importlib, importlib.metadata as m\nlist(m.entry_points(group='angelo.none'))\n"
                           "list(m.distributions())\np = m.FastPath('/angelo-matrix-missing-root')\n"
                           "p.lookup(p.mtime)\nimportlib.invalidate_caches()\n"),
    "importlib_resources": ("import importlib.resources as r\n"
                            "assert r.files('json').joinpath('__init__.py').read_text()\n"),
    "typing_cache": ("import typing\nassert typing.List[int] == typing.List[int]\n"
                     "assert typing.Optional[str] == typing.Union[str, None]\ntyping.Callable[[int], str]\n"
                     "typing.Literal['a']\n"),
    "enum_functional": ("import enum\nColor = enum.Enum('Color', 'RED GREEN')\nassert Color.RED.name == 'RED'\n"
                        "HELD.append(Color)\n"),
    "enum_class": ("import enum\nclass Mode(enum.IntFlag):\n    A = 1\n    B = 2\n"
                   "assert (Mode.A | Mode.B).value == 3\nHELD.append(Mode)\n"),
    "dataclass": ("import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass P:\n    x: int = 0\n"
                  "assert P(1) == P(1) and repr(P(2)) == 'P(x=2)'\nHELD.append(P)\n"),
    "abc_cache": ("import collections.abc\nclass S:\n    def __len__(self):\n        return 0\n"
                  "assert isinstance(S(), collections.abc.Sized)\nassert isinstance([], collections.abc.Sequence)\n"),
    "codecs_struct": "import codecs, struct\nassert struct.pack('<i', 1)\ncodecs.lookup('utf-8')\n'x'.encode('idna')\n",
    "inspect_signature": "import inspect, json\nassert inspect.signature(json.dumps).parameters\n",
    "singledispatch": ("import functools\n@functools.singledispatch\ndef f(x):\n    return 1\n"
                       "@f.register\ndef _(x: int):\n    return 2\nassert f(1) == 2 and f('a') == 1\nHELD.append(f)\n"),
    "logging": "import logging\nlogging.getLogger('angelo.matrix').debug('x')\n",
    "string_template": "import string\nassert string.Template('$a').substitute(a=1) == '1'\n",
    "traceback_linecache": "import traceback\nassert traceback.format_stack()\n",
}
CACHE_MODULES = ("functools", "fnmatch", "re", "ipaddress", "importlib.metadata", "importlib.resources", "typing",
                 "enum", "dataclasses", "abc", "collections.abc", "codecs", "encodings", "inspect", "logging",
                 "string", "linecache", "traceback")
