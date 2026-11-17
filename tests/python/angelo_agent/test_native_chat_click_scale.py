# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A GUI suite's click on the chat lands where the chat drew the control, at any interface scale (fail-first).

The chat lays out its controls in points and maps the pointer back with its scale (``chat_view.ui_scale()``: the
interface scale with the display's pixel size) and, in a right-to-left language, mirrored about the region
(``canvas_rtl.pointer_x``). A suite that added a chat rect to ``region.x``/``region.y`` as if it were in pixels
clicked beside the control whenever that scale is not 1: under Xvfb at 75 dpi (pixel size 0.78) about fifty suites
missed their first chat click and timed out (merge-11's VM GUI runs of 9d7a54eab7f; pipeline-gates fixed eight).

The one mapping is ``qa_input.chat_point`` / ``chat_rect`` (or ``window_click(..., scale=chat_view.ui_scale())``).
This test follows every value a suite or its helper module takes from the chat's canvas state (``chat_view._views``,
a flow window's ``chat_view_view``) through assignments, unpacking, loops, local and imported helper functions and
their parameters, and fails on any of them added to a region's or area's ``x``/``y`` by hand, or handed to
``window_click`` without the chat's scale. Other canvases (Studio, Flows, the cards) keep their hits in region
pixels; their values are never followed, so their raw arithmetic stays allowed."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
# GUI suites and the helper modules they import; this module's own tests and qa_input (the mapping) excepted.
SKIP = {"qa_input.py", Path(__file__).name}
SANITIZERS = {"chat_point", "chat_rect"}   # qa_input's mappings: what they return is window pixels.
SOURCES = {"chat_view_view"}               # flow_window: the canvas state of a chat shown inside a flow window.
# The chat state's fields the chat records in region pixels itself: marble_draw._region_rect applies the scale and
# the mirror to the waiting mark's square. Every other field (hits, geometry, the composer's rects) is in points.
PIXEL_FIELDS = {"marble_drawn"}


def scanned_files():
    return sorted(path for path in HERE.glob("*.py")
                  if path.name not in SKIP and not path.name.startswith(("test_native_", "conftest")))


def _call_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
START = (0, 0)
FULL, OWN = 2, 3   # A binding's flags: holds a chat value counting parameters given one, and without them.


def _pixel_field(node):
    """``state["marble_drawn"]`` or ``state.get("marble_drawn", ...)``: a field already in region pixels."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return node.slice.value in PIXEL_FIELDS
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args
            and isinstance(node.args[0], ast.Constant)):
        return node.args[0].value in PIXEL_FIELDS
    return False


def _end(node):
    return (node.end_lineno, node.end_col_offset)


class Module:
    """One file: its scopes, every name binding in order, and which of them hold chat values (``Project.solve``)."""

    def __init__(self, path):
        self.path = path
        self.tree = ast.parse(path.read_text(), str(path))
        self.owners = {}    # id(node) -> its innermost scope (function or comprehension; lambdas read their enclosing one)
        self.parents = {}   # scope -> enclosing scope
        self._own(self.tree, self.tree)
        self.functions = {}   # name -> FunctionDef (a later definition of a name wins)
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
        self.imports = {}   # local name -> (module stem, name in it, or None for the module itself)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module and "." not in node.module:
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = (node.module, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "." not in alias.name:
                        self.imports[alias.asname or alias.name] = (alias.name, None)
        # Bindings: scope -> name -> [[position, value node or None, tainted]] in source order.
        self.bindings = {}
        for node in ast.walk(self.tree):
            for target, value, position in self._binds(node):
                scope = target.function if isinstance(target, _Param) else self.owners.get(id(target), self.tree)
                for name in _target_names(target):
                    self.bindings.setdefault(scope, {}).setdefault(name, []).append([position, value, False, False])
        for records in self.bindings.values():
            for entries in records.values():
                entries.sort(key=lambda entry: entry[0])
        self.returns = set()      # function names returning chat values of their own
        self.passes = set()       # function names returning what their arguments give (a call's value is its arguments')
        self.parameters = set()   # (function name, parameter name) given chat values at some call

    def _own(self, node, scope):
        for child in ast.iter_child_nodes(node):
            self.owners[id(child)] = scope
            inner = child if isinstance(child, SCOPES) else scope
            if inner is child:
                self.parents[child] = scope
            self._own(child, inner)

    @staticmethod
    def _binds(node):
        if isinstance(node, ast.Assign):
            return [(target, node.value, _end(node)) for target in node.targets]
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            return [(node.target, node.value, _end(node))]
        if isinstance(node, ast.AugAssign):
            return [(node.target, node, _end(node))]   # The target's old value and the added one.
        if isinstance(node, (ast.For, ast.AsyncFor)):
            return [(node.target, node.iter, _end(node.iter))]
        if isinstance(node, ast.NamedExpr):
            return [(node.target, node.value, _end(node))]
        if isinstance(node, ast.withitem) and node.optional_vars is not None:
            return [(node.optional_vars, node.context_expr, _end(node.context_expr))]
        if isinstance(node, ast.comprehension):   # Its own scope: bound for every element of the comprehension.
            return [(node.target, node.iter, START)]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            return [(_Param(argument, node), None, START) for argument in arguments]
        return []


class _Param(ast.Name):
    """A parameter as a binding target in its function's scope."""

    def __init__(self, argument, function):
        super().__init__(id=argument.arg)
        self.function = function


def _target_names(target):
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _target_names(element)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


class Project:
    def __init__(self, paths):
        self.modules = {path.stem: Module(path) for path in paths}

    def name_tainted(self, module, scope, name, position, mode=FULL):
        """Whether ``name`` holds a chat value at ``position`` in ``scope``: its latest binding before there, else
        (a loop's back edge) its last one; a name never bound here is the enclosing scope's."""
        while True:
            entries = module.bindings.get(scope, {}).get(name)
            if entries:
                before = [entry for entry in entries if entry[0] < position]
                return (before or entries)[-1][mode]
            if scope is module.tree:
                return False
            scope = module.parents.get(scope, module.tree)

    def tainted(self, module, node, mode=None):
        """Whether any part of ``node`` reads the chat's canvas state, or a name or helper holding a value from it.
        A ``chat_point``/``chat_rect`` call maps such a value to window pixels: nothing under it counts. ``OWN``
        counts every parameter as plain: what a function makes of chat values by itself (``solve``)."""
        mode = FULL if mode is None else mode
        stack = [node]
        while stack:
            sub = stack.pop()
            if isinstance(sub, ast.Call) and _call_name(sub.func) in SANITIZERS:
                continue
            if _pixel_field(sub):
                continue
            if (isinstance(sub, ast.Attribute) and sub.attr == "_views" and isinstance(sub.value, ast.Name)
                    and sub.value.id == "chat_view"):
                return True
            if isinstance(sub, ast.Call) and (_call_name(sub.func) in SOURCES or self.call_tainted(module, sub, mode)):
                return True
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                scope = module.owners.get(id(sub), module.tree)
                if self.name_tainted(module, scope, sub.id, (sub.lineno, sub.col_offset), mode):
                    return True
            if isinstance(sub, ast.comprehension):   # What it yields comes from its iterable, never its filters.
                stack.extend((sub.target, sub.iter))
                continue
            stack.extend(ast.iter_child_nodes(sub))
        return False

    def resolve(self, module, func):
        """The (module, FunctionDef) a call's ``func`` names in the scanned files, or (None, None)."""
        name = _call_name(func)
        if isinstance(func, ast.Name) and name in module.functions:
            return module, module.functions[name]
        if isinstance(func, ast.Name) and name in module.imports:
            stem, imported = module.imports[name]
            if imported and stem in self.modules and imported in self.modules[stem].functions:
                return self.modules[stem], self.modules[stem].functions[imported]
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            stem, imported = module.imports.get(func.value.id, (None, "x"))
            if imported is None and stem in self.modules and name in self.modules[stem].functions:
                return self.modules[stem], self.modules[stem].functions[name]
        return None, None

    def call_tainted(self, module, call, mode):
        target, function = self.resolve(module, call.func)
        if function is None:
            return False
        if function.name in target.returns:
            return True
        return function.name in target.passes and any(
            self.tainted(module, argument, mode) for argument in [*call.args, *(k.value for k in call.keywords)])

    def solve(self):
        changed = True
        while changed:
            changed = False
            for module in self.modules.values():
                for scope, records in module.bindings.items():
                    for name, entries in records.items():
                        for entry in entries:
                            value = entry[1]
                            for mode in (FULL, OWN):
                                if entry[mode]:
                                    continue
                                if value is None:   # A parameter: given a chat value at some call (never OWN).
                                    now = mode == FULL and isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                                        and (scope.name, name) in module.parameters
                                elif isinstance(value, ast.AugAssign):
                                    now = self.tainted(module, value.value, mode) or self.name_tainted(
                                        module, scope, name, (value.lineno, value.col_offset), mode)
                                else:
                                    now = self.tainted(module, value, mode)
                                if now:
                                    entry[mode] = True
                                    changed = True
                for node in ast.walk(module.tree):
                    if isinstance(node, ast.Return) and node.value is not None:
                        scope = module.owners.get(id(node), module.tree)
                        while scope is not module.tree and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            scope = module.parents.get(scope, module.tree)
                        if scope is module.tree or scope.name in module.returns:
                            continue
                        if self.tainted(module, node.value, OWN):
                            module.returns.add(scope.name)
                            changed = True
                        elif scope.name not in module.passes and self.tainted(module, node.value):
                            module.passes.add(scope.name)   # Its value is its arguments' (a pass-through helper).
                            changed = True
                    elif isinstance(node, ast.Call):
                        changed |= self._propagate_call(module, node)

    def _propagate_call(self, module, call):
        target_module, function = self.resolve(module, call.func)
        if function is None:
            return False
        changed = False
        parameters = [a.arg for a in function.args.posonlyargs + function.args.args]
        given = [(parameters[i], argument) for i, argument in enumerate(call.args) if i < len(parameters)]
        given += [(keyword.arg, keyword.value) for keyword in call.keywords if keyword.arg]
        for parameter, argument in given:
            key = (function.name, parameter)
            if key not in target_module.parameters and self.tainted(module, argument):
                target_module.parameters.add(key)
                changed = True
        return changed

    def violations(self):
        found = {}
        for module in self.modules.values():
            for node in ast.walk(module.tree):
                if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
                    terms = self._terms(node)
                    positions = [t for t in terms if isinstance(t, ast.Attribute) and t.attr in ("x", "y")]
                    # A region's or area's own size is window pixels, whatever the name holding it was read with.
                    own = [t for t in terms if isinstance(t, ast.Attribute) and t.attr in ("width", "height")]
                    if positions and any(self.tainted(module, t) for t in terms if t not in positions and t not in own):
                        found.setdefault((module.path.name, node.lineno), ast.unparse(node)[:100])
                elif isinstance(node, ast.Call) and _call_name(node.func) == "window_click":
                    if (len(node.args) > 2 and self.tainted(module, node.args[2])
                            and not any(k.arg == "scale" for k in node.keywords)):
                        found.setdefault((module.path.name, node.lineno), "window_click without the chat's scale")
        return ["%s:%d: %s" % (name, line, text) for (name, line), text in sorted(found.items())]

    @classmethod
    def _terms(cls, node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            return cls._terms(node.left) + cls._terms(node.right)
        return [node]


def _project(sources):
    """A project from {file name: source} (the checker's own tests)."""
    import tempfile
    folder = Path(tempfile.mkdtemp())
    for name, source in sources.items():
        (folder / name).write_text(source)
    return Project(sorted(folder.glob("*.py")))


class CheckerTests(unittest.TestCase):
    """The checker sees the shapes suites use, and leaves other canvases' pixel arithmetic alone."""

    def flagged(self, sources):
        project = _project(sources)
        project.solve()
        return project.violations()

    def test_a_hit_rect_added_to_the_region_is_found_through_helpers_and_parameters(self):
        source = '''
from angelo_agent import chat_view
def view():
    return chat_view._views.get(1, {})
def hit(action):
    return next(r for r, a, v in view()["hits"] if a == action)
def at(rect, region):
    return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
def click(region, action):
    x, y, w, h = hit(action)
    return at(hit(action), region), region.x + x
'''
        found = self.flagged({"test_blender_a.py": source})
        self.assertEqual([line.split(":")[1] for line in found], ["8", "11"], found)

    def test_an_imported_helper_and_its_module_are_followed(self):
        helper = '''
from angelo_agent import chat_view
def send_rect(area):
    return chat_view._views[area]["geometry"]["send"]
'''
        suite = '''
import helper_qa
from helper_qa import send_rect
def go(region, area):
    a = send_rect(area)
    b = helper_qa.send_rect(area)
    first = region.x + a[0]
    return first, region.y + b[1]
'''
        found = self.flagged({"helper_qa.py": helper, "test_blender_b.py": suite})
        self.assertEqual(len(found), 2, found)

    def test_the_mappings_and_other_canvases_are_not_flagged(self):
        source = '''
import qa_input
from angelo_agent import chat_view, studio_input
def go(window, region, area):
    rect = chat_view._views[area]["hits"][0][0]
    x, y = qa_input.chat_point(region, rect)
    box = qa_input.chat_rect(region, rect)
    studio = studio_input.state()["hits"][0][0]
    qa_input.window_click(window, region, rect, "send", scale=chat_view.ui_scale())
    return region.x + studio[0], region.y + region.height / 2, x + 1, box[0] + 2
'''
        self.assertEqual(self.flagged({"test_blender_c.py": source}), [])

    def test_a_filter_naming_a_chat_value_yields_no_chat_value(self):
        source = '''
from angelo_agent import chat_view, session_rail
def go(region, area):
    chat = chat_view._views[area]
    tile = next(t for t in session_rail.tiles() if t.session == chat["session"])
    return region.x + tile.rect[0]
'''
        self.assertEqual(self.flagged({"test_blender_e.py": source}), [])

    def test_a_pass_through_helper_carries_only_its_own_arguments(self):
        source = '''
from angelo_agent import chat_view, session_rail
def until(read):
    return read()
def go(region, area):
    rect = until(lambda: chat_view._views[area]["hits"][0][0])
    tile = until(lambda: session_rail.tiles()[0])
    first = region.x + tile.rect[0]
    return first, region.x + rect[0]
'''
        self.assertEqual([line.split(":")[1] for line in self.flagged({"test_blender_f.py": source})], ["9"])

    def test_window_click_without_the_scale_is_flagged(self):
        source = '''
import qa_input
from angelo_agent import chat_view
def go(window, region, area):
    rect = chat_view._views[area]["hits"][0][0]
    qa_input.window_click(window, region, rect, "send")
'''
        self.assertEqual(len(self.flagged({"test_blender_d.py": source})), 1)


class GuiSuitesTests(unittest.TestCase):
    def test_no_suite_adds_a_chat_rect_to_window_pixels_by_hand(self):
        project = Project(scanned_files())
        project.solve()
        found = project.violations()
        self.assertEqual(found, [], "Map chat points with qa_input.chat_point/chat_rect (or window_click(scale=...)):\n"
                         + "\n".join(found))


class MappingTests(unittest.TestCase):
    """qa_input.chat_point/chat_rect: the inverse of chat_view's pointer mapping (scale, then the RTL mirror)."""

    def setUp(self):
        sys.path.insert(0, str(HERE))
        import qa_input
        self.qa = qa_input

    def region(self, x=100, y=50, width=400):
        from types import SimpleNamespace
        return SimpleNamespace(x=x, y=y, width=width, height=300, type="WINDOW")

    def test_points_scale_into_window_pixels(self):
        for got, want in zip(self.qa.chat_rect(self.region(), (10, 20, 40, 8), scale=0.78, rtl=False),
                             (107.8, 65.6, 31.2, 6.24)):
            self.assertAlmostEqual(got, want)
        self.assertEqual(self.qa.chat_point(self.region(), (10, 20, 40, 8), scale=2.0, rtl=False), (160, 98))

    def test_right_to_left_mirrors_about_the_region(self):
        # chat_view: x_points = pointer_x(px - region.x, width) / scale, pointer_x(x, w) = w - x in RTL.
        left, bottom, width, height = self.qa.chat_rect(self.region(), (10, 20, 40, 8), scale=1.0, rtl=True)
        self.assertEqual((left, width), (100 + 400 - 50, 40))
        x, _y = self.qa.chat_point(self.region(), (10, 20, 40, 8), scale=1.0, rtl=True)
        self.assertEqual(x, 100 + 400 - 30)
        # The inverse: the point maps back into the rect as chat_view maps the pointer.
        back = (400 - (x - 100)) / 1.0
        self.assertTrue(10 <= back <= 50)


if __name__ == "__main__":
    unittest.main()
