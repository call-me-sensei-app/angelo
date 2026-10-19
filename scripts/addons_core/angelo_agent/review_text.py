# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Review & Accept card's words: a preview's result as an artist reads it. Display only.

``sections(descriptor)`` turns a retained preview (``agent_workspace_ui.preview_descriptor``) into headed
lists for the card (``review_card``): what is added, changed and removed, each object by name with its kind
in words ("Mesh · 8 vertices"), the scene's object count before and after, every check with whether it
passed, and a caveat sentence only when one applies (some changes not listed, or not read back).
``exact_settings`` is the submitted settings, exactly, for the card's mono box.

Nothing here is stored, sent to the agent or used to decide anything: the operator's review lines, the review
fingerprint and the acceptance are unchanged (``agent_workspace_ui``). Text is translated when drawn
(``ui_text``); names and numbers are the scene's own and are never translated.
"""

from collections.abc import Mapping

from .ui_text import iface_, n_, plural_

KINDS = {"MESH": n_("Mesh", "Angelo"), "CURVE": n_("Curve", "Angelo"), "SURFACE": n_("Surface", "Angelo"),
         "META": n_("Metaball", "Angelo"), "FONT": n_("Text"), "CURVES": n_("Hair curves", "Angelo"),
         "POINTCLOUD": n_("Point cloud", "Angelo"), "VOLUME": n_("Volume", "Angelo"),
         "GREASEPENCIL": n_("Grease Pencil", "Angelo"), "ARMATURE": n_("Armature", "Angelo"),
         "LATTICE": n_("Lattice", "Angelo"), "EMPTY": n_("Empty", "Angelo"), "LIGHT": n_("Light", "Angelo"),
         "LIGHT_PROBE": n_("Light probe", "Angelo"), "CAMERA": n_("Camera", "Angelo"),
         "SPEAKER": n_("Speaker", "Angelo")}
GROUPS = (("added", n_("Added", "Angelo")), ("modified", n_("Changed", "Angelo")), ("removed", n_("Removed", "Angelo")))
PASSED, FAILED, UNCHECKED = n_("Passed", "Angelo"), n_("Failed"), n_("Not verified", "Angelo")


def _kind(value):
    from .agent_workspace_ui import readable_label
    return iface_(KINDS[value]) if value in KINDS else readable_label(value)


def _object(entry, group):
    """One object's line: its name, its kind in words and what is known about it."""
    from .agent_workspace_ui import readable_label
    if not isinstance(entry, Mapping):
        return str(entry)
    parts = [str(entry.get("name") or entry.get("handle") or iface_("Unnamed object"))]
    if entry.get("type"):
        parts.append(_kind(entry["type"]))
    if entry.get("type") == "MESH" and group == "added":
        parts.append(plural_(int(entry.get("vertices") or 0), "{count} vertex", "{count} vertices"))
    if entry.get("fields"):
        parts.append(", ".join(readable_label(field).lower() for field in entry["fields"]))
    if entry.get("properties"):
        parts.append(iface_("settings {paths}", paths=", ".join(str(path) for path in entry["properties"])))
    return " · ".join(parts)


def _status(check):
    status = check.get("status", check.get("passed"))
    if status is True or str(status).lower() in {"pass", "passed", "ok", "true"}:
        return iface_(PASSED)
    if status is False or str(status).lower() in {"fail", "failed", "false"}:
        return iface_(FAILED)
    return iface_(UNCHECKED) if status is None else str(status).replace("_", " ").capitalize()


def _checks(checks):
    if isinstance(checks, Mapping):
        checks = [{"name": key, **value} if isinstance(value, Mapping) else {"name": key, "status": value}
                  for key, value in checks.items()]
    return [check for check in checks or () if isinstance(check, Mapping)]


def sections(descriptor):
    """``[(style, text)]`` for ``card_kit.Builder.scrollbox``: ``heading`` rows and ``item`` rows."""
    from .agent_workspace_ui import readable_label
    changes = descriptor.get("changes") or {}
    rows = []
    for group, heading in GROUPS:
        entries = changes.get(group) or ()
        if entries:
            rows.append(("heading", iface_("{group} · {count}", group=iface_(heading), count=len(entries))))
            rows += [("item", _object(entry, group)) for entry in entries]
    if not any(changes.get(group) for group, _ in GROUPS):
        rows.append(("heading", iface_("Nothing is added, changed or removed.")))
    before, after = changes.get("object_count_before"), changes.get("object_count_after")
    if before is not None and after is not None:
        rows.append(("heading", iface_("Objects in the scene: {before} → {after}", before=before, after=after)))
    if changes.get("truncated"):
        rows.append(("item", iface_("Some changes are not listed here; the counts include them.")))
    if changes.get("uncertain"):
        rows.append(("item", iface_("Some changes could not be read back. Check the exact settings before you accept.")))
    checks = _checks(descriptor.get("checks"))
    if checks:
        rows.append(("heading", iface_("Checks · {count}", count=len(checks))))
        for check in checks:
            name = readable_label(check.get("name") or check.get("label") or check.get("kind") or iface_("Unnamed check"))
            rows.append(("item", iface_("{check} · {result}", check=name, result=_status(check))))
    else:
        rows.append(("heading", iface_("No checks were run on this preview.")))
    return rows


def exact_settings(descriptor):
    """The submitted settings, exactly (every control, value and target), for the card's mono box."""
    from .agent_workspace_ui import control_lines
    return control_lines(descriptor.get("arguments", {}), target_labels=descriptor.get("target_labels"))
