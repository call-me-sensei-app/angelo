# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Extract Angelo's interface text into ``locale/angelo/angelo.pot`` (run INSIDE Angelo).

    Angelo --background --factory-startup --python scripts/angelo/i18n/extract_pot.py -- [--out PATH]

Uses Blender's own extractor (``_bl_i18n_utils``) so contexts are exactly what
the runtime looks up: the RNA dump (operator, panel and property names, enum
items), the Python UI scan (``text=``, ``iface_`` and friends), and the C/C++
scan (``IFACE_``, ``TIP_``, ``BKE_report``). The result is filtered to what
Angelo owns: the add-on, the startup scripts, the changed C++ files, and a short
list of strings Angelo adds to Blender's own RNA (``sources.EXTRA_MSGIDS``).
Counted phrases (``plural_``) and chat notices (``notice``) are not Blender
spellings, so a small AST pass adds them. The output is deterministic (sorted,
source files without line numbers) so a catalogue diff shows only real changes.
"""

import argparse
import ast
import os
import re
import sys
import time
from pathlib import Path

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parent))

from _bl_i18n_utils import bl_extract_messages as extractor, settings as settings_i18n, utils  # noqa: E402
from i18n import po, scan, sources  # noqa: E402

SOURCE_PREFIX = ":src: "
ANGELO_RNA = re.compile(r"(?i)angelo")
PLURAL_CONTEXTS = ("zero", "one", "two", "few", "many", "other")


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(sources.POT))
    parser.add_argument("--source-dir", default=str(sources.ROOT))
    return parser.parse_args(argv)


def angelo_c_files():
    """C++ files that differ from Blender's own: the list lives in ``sources.CPP_FILES``."""
    return {path.relative_to(sources.ROOT).as_posix() for path in sources.CPP_FILES}


def owned(source_comment):
    """Does this ``#. :src:`` comment point at Angelo-owned code?"""
    location = source_comment.split(SOURCE_PREFIX, 1)[-1]
    path = normalize_source(location)
    path = "angelo_agent/" + path.split("angelo_agent/", 1)[1] if "angelo_agent/" in path else path
    if "angelo_agent/" in path or "angelo_default_layout" in path or "_console_angelo" in path:
        return True
    if path in angelo_c_files() or path in sources.SHARED_PYTHON_FILES:
        return True
    return bool(ANGELO_RNA.search(location)) and ("bpy.types" in location or "Angelo" in location)


def normalize_source(location):
    """A source location without line numbers or machine paths: ``angelo_agent/ui.py``, ``scripts/startup/...``."""
    location = re.sub(r":\d+$", "", location)
    for marker in ("scripts/addons_core/", "scripts/"):
        if marker in location:
            tail = location.split(marker, 1)[1]
            return tail if marker.endswith("addons_core/") else marker + tail
    return location


def blender_reference(root):
    """Every msgid Blender's own catalogues translate, from the two most complete ones."""
    known = set()
    for name in ("zh_HANS", "ja"):
        _, entries = po.read(str(root / "locale/po" / f"{name}.po"))
        known.update(entry.id for entry in entries)
    return known


def operator_property_messages():
    """(ctxt, msgid, source) for the properties of Angelo's operators: names, descriptions and enum items.

    Blender's RNA dump does not reach a Python operator's properties (they live on its OperatorProperties
    struct, which it walks only for C operators), yet a dialog draws them and Blender translates them there.
    """
    found = []
    for name in dir(bpy.types):
        if not name.startswith("ANGELO_OT_"):
            continue
        try:
            properties = getattr(bpy.ops.angelo, name[len("ANGELO_OT_"):]).get_rna_type().properties
        except (AttributeError, KeyError, RuntimeError):
            continue
        for prop in properties:
            if prop.identifier == "rna_type":
                continue
            origin = f"bpy.types.{name}.{prop.identifier}"
            ctxt = "" if prop.translation_context in ("", "*") else prop.translation_context
            if prop.name and prop.name != prop.identifier:
                found.append((ctxt, prop.name, origin))
            if prop.description:
                found.append(("", prop.description, origin))
            for item in (prop.enum_items if prop.type == 'ENUM' else ()):
                if item.name and item.name != item.identifier:
                    found.append((ctxt, item.name, f"{origin}:'{item.identifier}'"))
                if item.description:
                    found.append(("", item.description, f"{origin}:'{item.identifier}'"))
    return found


def explicit_contexts(source):
    """{(line, msgid): context} for marks given a literal context, ``n_("Keys", "Angelo")``, as Blender's extractor reads them."""
    found = {}
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in ("n_", "iface_", "tip_", "rpt_")
                and len(node.args) >= 2 and literal(node.args[0]) and literal(node.args[1])):
            found[(node.lineno, literal(node.args[0]))] = literal(node.args[1])  # The call's line, as scan records it.
    return found


def python_literals(root):
    """``(lookup_only, literals)``, each {normalized source: {string}}: literals a Python source uses only as lookup
    keys (``scan.literal_roles``), and every string literal it contains."""
    lookup_only, literals = {}, {}
    files = sorted((root / "scripts/addons_core/angelo_agent").glob("*.py"))
    files += [root / name for name in sources.STARTUP_FILES + sources.SHARED_PYTHON_FILES]
    for path in files:
        if path.exists():
            source, name = path.read_text(encoding="utf-8"), normalize_source(str(path))
            lookups, others = scan.literal_roles(source)
            lookup_only[name], literals[name] = lookups, lookups | others
    return lookup_only, literals


def blender_extractor_artifact(msgid, origins, lookup_only, literals):
    """True for a message Blender's Python extractor made up rather than found.

    It walks every literal inside a translated argument, so ``text=record["name"]`` gives it "name", and it joins
    the literals of one argument, so ``report(..., result.get("error") or rpt_("Nothing"))`` gives "errorNothing".
    A message from Python sources only is real when one of them contains it as a literal that is not only a key.
    """
    python = [origin for origin in origins if origin in literals]
    if not python or len(python) != len(origins):
        return False
    return not any(msgid in literals[origin] and msgid not in lookup_only[origin] for origin in python)


def literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def plural_and_notice_messages(paths):
    """(ctxt, msgid, source) for ``plural_`` families and ``notice`` templates the Blender extractor cannot see."""
    found = []
    for path in paths:
        relative = normalize_source(path.relative_to(sources.ROOT).as_posix())
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
            if name == "notice" and node.args and literal(node.args[0]):
                found.append(("", literal(node.args[0]), relative))
            elif name == "plural_" and len(node.args) >= 3 and literal(node.args[1]) and literal(node.args[2]):
                one, other = literal(node.args[1]), literal(node.args[2])
                found.append(("plural:one", one, relative))
                found += [("plural:" + category, other, relative) for category in PLURAL_CONTEXTS if category != "one"]
    return found


def main():
    args = parse_args()
    root = Path(args.source_dir)
    settings = settings_i18n.I18nSettings()
    settings.load(None)
    settings.SOURCE_DIR = str(root)
    pot = utils.I18nMessages.gen_empty_messages(settings.PARSER_TEMPLATE_ID, "Angelo 1.0", "", time.gmtime(),
                                                settings=settings)
    msgs = pot.msgs
    reports = extractor._gen_reports(None)

    import addon_utils
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    bundled = Path(os.path.realpath(Path(bpy.utils.system_resource('SCRIPTS')) / "addons_core/angelo_agent"))
    if bundled != (root / "scripts/addons_core/angelo_agent").resolve():
        print(f"warning: the app's add-on is {bundled}, not this checkout's; operator and property text comes from it")
    extractor.dump_rna_messages(msgs, reports, settings)
    operator_properties = operator_property_messages()
    # Python text comes from this checkout (``--source-dir``), not from whatever the app bundled, so the
    # template always matches the sources it is committed with; the RNA dump above needs the app's add-on,
    # which must therefore be this checkout's (a development app links it).
    # Blender's extractor reads the UI modules (it also takes literals of UI-looking calls, which elsewhere are
    # data: ``label(record["name"])``); every other module contributes only what it explicitly marks.
    addon = sorted((root / "scripts/addons_core/angelo_agent").glob("*.py"))
    files = [path for path in addon if path.stem in sources.UI_MODULES]
    files += [root / name for name in sources.STARTUP_FILES + sources.SHARED_PYTHON_FILES]
    extractor.dump_py_messages_from_files(msgs, reports, [str(f) for f in files if f.exists()], settings)
    extractor.dump_src_messages(msgs, reports, settings)

    blender_msgids = blender_reference(root)
    cpp_files = angelo_c_files() | set(sources.SHARED_PYTHON_FILES)
    lookup_only, literals = python_literals(root)
    artifacts = []
    entries = {}
    for (ctxt, msgid), message in msgs.items():
        if not msgid:
            continue
        ctxt = "" if ctxt == settings.DEFAULT_CONTEXT else ctxt  # One spelling of the default context: "".
        comments = [line for line in message.comment_lines if SOURCE_PREFIX in line]
        keep = [c.split(SOURCE_PREFIX, 1)[1] for c in comments if owned(c)]
        # Blender's C++ files are mostly Blender's own text: keep only what Blender's catalogues do not know.
        if keep and all(normalize_source(s) in cpp_files for s in keep) and msgid in blender_msgids:
            continue
        origins = {normalize_source(s) for s in keep}
        if keep and blender_extractor_artifact(msgid, origins, lookup_only, literals):
            artifacts.append(msgid)
            continue
        if keep:
            entries[(ctxt, msgid)] = origins
    for path in addon:
        if path.stem not in sources.UI_MODULES and path.stem not in sources.MODEL_FACING:
            source = path.read_text(encoding="utf-8")
            contexts = explicit_contexts(source)
            for msgid, line, wrapper in scan.scan(source, path.name, net=False)[1]:
                if wrapper not in ("plural_", "notice"):  # Those two carry their own contexts (below).
                    ctxt = "Operator" if wrapper == "operator" else contexts.get((line, msgid), "")
                    entries.setdefault((ctxt, msgid), set()).add("angelo_agent/" + path.name)
    for ctxt, msgid, origin in operator_properties:
        entries.setdefault((ctxt, msgid), set()).add(origin)
    for ctxt, msgid, origin in sources.EXTRA_MSGIDS:
        entries.setdefault((ctxt, msgid), set()).add(origin)
    repo_files = sorted((sources.ADDON).glob("*.py")) + [sources.ROOT / f for f in sources.STARTUP_FILES]
    for ctxt, msgid, origin in plural_and_notice_messages(repo_files):
        entries.setdefault((ctxt, msgid), set()).add(origin)

    header = "\n".join([
        "Project-Id-Version: Angelo 1.0",
        "Report-Msgid-Bugs-To: ",
        "Language: \n",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "Content-Transfer-Encoding: 8bit",
        "X-Generator: scripts/angelo/i18n/extract_pot.py",
    ])
    result = [po.Entry(msgid, "", ctxt, extracted=[f"src: {s}" for s in sorted(origins)])
              for (ctxt, msgid), origins in sorted(entries.items(), key=lambda kv: (kv[0][1].lower(), kv[0][0]))]
    po.write(args.out, header, result)
    for msgid in sorted(set(artifacts)):
        print(f"skipped (not a literal in its sources): {msgid[:100]!r}")
    print(f"Angelo POT: {len(result)} messages -> {args.out}")


if __name__ == "__main__":
    main()
