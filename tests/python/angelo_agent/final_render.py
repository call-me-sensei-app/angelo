# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which file is a trial's final render: the one the agent names as final, by one rule for every side.

A brief asks for a render by name (``tree-final.png``). Angelo never
overwrites: a render to a taken name writes the next free one
(``tree-final-j3.png``), and its job receipt and the agent's reply name that
file. A grader that looked only at the requested name would judge an older
render. So the file judged is the one the agent names as its final output,
decided in this order (``decide``); the source is kept with the verdict:

1. ``reply``: the turn's final reply names a file that answers to the
   requested name (below) and is one of the run's files; when it names
   several, the one written last.
2. ``job receipt``: the last render the side ran for the requested name
   wrote another file. Angelo's receipt says which (``receipts``). On the
   Blender MCP side a render asked for a name writes that name (stock Blender
   overwrites), so there this rule never names another file.
3. ``requested path``: nothing names another file: the requested name, as
   before.

A file answers to the requested name when it has that name, or when a file of
that name is beside it (the name was taken) and its name is the requested
stem followed by a suffix, with the same extension (``tree-final-j3.png``,
``tree-final-2.png``). A name the brief did not ask for never stands in for
it: a reply naming ``render.png`` names nothing here.

The named file is judged whatever else exists. ``newest`` says whether it is
the newest of the run's files that answer to the requested name; when it is
not, ``newest_file`` names that one, so the report shows the agent named an
older render. Dependency-free; read-only.
"""

import os
import re

# An image file name: no path separators or quotes in it, and not the start of a longer name (``a.png.bak``).
IMAGE_NAME = re.compile(r"([^\s\"'`<>|*?\\/]+\.(?:png|jpe?g|exr|tiff?|webp|bmp))(?!\w)(?!\.\w)", re.IGNORECASE)
SOURCES = ("reply", "job receipt", "requested path")
SLACK_S = 2.0   # A file written this long before the message was sent is another run's (as the output check).


def _split(name):
    stem, extension = os.path.splitext(os.path.basename(str(name)))
    return stem.lower(), extension.lower()


def answers(name, requested, taken=True):
    """Whether a file name answers to the requested one: that name, or (``taken``: a file of the requested name
    is beside it) the requested stem followed by a suffix, with the same extension."""
    stem, extension = _split(name)
    wanted, wanted_extension = _split(requested)
    if extension != wanted_extension or not stem.startswith(wanted):
        return False
    return stem == wanted or bool(taken)


def mentioned(text):
    """File names (base names) of images a text names, in the order it first names them."""
    found = []
    for match in IMAGE_NAME.finditer(str(text or "")):
        name = match.group(1).strip("*_.,:;()[]")
        if name and name.lower() not in [entry.lower() for entry in found]:
            found.append(name)
    return found


def variants(requested, paths):
    """The paths whose files answer to the requested name: its own name always, a suffixed one only when a file of
    the requested name is in the same folder."""
    name = os.path.basename(str(requested))
    taken = {os.path.dirname(os.path.abspath(path)) for path in paths if os.path.basename(path).lower() == name.lower()}

    def is_taken(folder):
        return folder in taken or os.path.isfile(os.path.join(folder, name))
    return [path for path in paths if answers(path, requested, is_taken(os.path.dirname(os.path.abspath(path))))]


def stated(text, requested):
    """Absolute or home-relative paths a text states (a reply, a script) whose files answer to the requested
    name, in order (the output check looks there too)."""
    found = []
    for match in re.finditer(r"(~?/[^\s\"'`<>|*?\\]+)", str(text or "")):
        path = os.path.expanduser(match.group(1).rstrip(".,:;)]"))
        if answers(path, requested) and path not in found:
            found.append(path)
    return [path for path in found[:50] if os.path.isfile(path)]


def find(requested, roots, max_depth=4, max_entries=20000):
    """Files under ``roots`` (not in hidden folders, shallow first) that answer to the requested name."""
    found, visited = [], 0
    wanted, extension = _split(requested)
    for root in roots:
        root = os.path.abspath(str(root))
        for folder, folders, files in os.walk(root):
            depth = 0 if folder == root else os.path.relpath(folder, root).count(os.sep) + 1
            folders[:] = sorted(d for d in folders if not d.startswith(".")) if depth < max_depth else []
            visited += len(files)
            found += [os.path.join(folder, f) for f in sorted(files)
                      if _split(f)[1] == extension and _split(f)[0].startswith(wanted)]
            if visited > max_entries:
                break
    return variants(requested, list(dict.fromkeys(found)))


def _modified(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def receipt_file(requested, receipts):
    """The file the last finished render for the requested name wrote, from Angelo's job records
    (``[{"kind", "status", "output": asked, "wrote": written}]`` in order), or None."""
    wanted = os.path.basename(str(requested)).lower()
    last = None
    for job in receipts or ():
        if (job.get("kind") == "RENDER_STILL" and job.get("status") == "done"
                and os.path.basename(str(job.get("output") or "")).lower() == wanted):
            last = os.path.basename(str(job.get("wrote") or job.get("output")))
    return last


def decide(requested, paths, reply="", receipts=(), since=None):
    """The final render among ``paths`` (the run's candidate files) by the module's rule.

    Returns ``{"file", "source", "named", "newest", "newest_file"}``: ``file`` is the base name to judge,
    ``source`` one of ``SOURCES``, ``named`` the answering names the reply gave, ``newest`` whether ``file`` is
    the newest of this run's answering files (None when none exists).
    """
    answering = [path for path in variants(requested, list(dict.fromkeys(paths)))
                 if since is None or (_modified(path) or 0) >= since - SLACK_S]
    by_name = {}
    for path in answering:
        by_name.setdefault(os.path.basename(path).lower(), []).append(path)
    named = [name for name in mentioned(reply) if name.lower() in by_name]
    if named:
        latest = max(named, key=lambda name: max(_modified(path) or 0 for path in by_name[name.lower()]))
        choice, source = latest, "reply"
    else:
        wrote = receipt_file(requested, receipts)
        if wrote is not None and wrote.lower() in by_name:
            choice, source = wrote, "job receipt"
        else:
            choice, source = os.path.basename(str(requested)), "requested path"
    newest_path = max(answering, key=lambda path: _modified(path) or 0) if answering else None
    chosen = by_name.get(choice.lower(), [])
    newest = None
    if newest_path is not None and chosen:
        newest = max(_modified(path) or 0 for path in chosen) >= (_modified(newest_path) or 0)
    return {"file": choice, "source": source, "named": named, "newest": newest,
            "newest_file": None if newest in (None, True) else os.path.basename(newest_path)}


def judged(paths, decision):
    """The candidate paths of the decided file, in their order (the output check tries each)."""
    return [path for path in paths if os.path.basename(path).lower() == decision["file"].lower()]


def describe(decision):
    """``tree-final-j3.png (named in the reply; the newest render)``: the judged file and why, for reports."""
    if not decision:
        return "unknown"
    why = {"reply": "named in the reply", "job receipt": "the last render's job receipt",
           "requested path": "the requested name; nothing named another"}[decision["source"]]
    if decision.get("newest") is False:
        newest = "not the newest render: %s is newer" % decision.get("newest_file")
    else:
        newest = "the newest render" if decision.get("newest") else "no render of this run"
    return "%s (%s; %s)" % (decision["file"], why, newest)
