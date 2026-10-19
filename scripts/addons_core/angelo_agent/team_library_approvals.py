# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Approval cards for the team-library effects: what is downloaded or uploaded, from or to whom.

``approvals.approval_message`` routes ``angelo_team_*`` here. The card names the
item (from what earlier results showed, ``team_library_results.known``) or
what will be uploaded, the organization and space, and the effect Blender Undo
can and can't reverse. Card text is translated where it is drawn (whole
sentences, named placeholders); item titles and people are the organization's
own words. Standard library only; no bpy.
"""

from . import team_library_access, team_library_results
from .ui_text import iface_, plural_


def _organization():
    account = team_library_access.account()
    view = account.view() if account is not None else None
    return (view.org_name or iface_("your team"), view.org_id) if view is not None and view.signed_in else (iface_("your team"), "")


def _size(value):
    if not isinstance(value, int) or value <= 0:
        return iface_("size checked on download")
    return "%.1f MB" % (value / 1048576) if value >= 104858 else "%d KB" % max(1, value // 1024)


def _upload_line(source, target, title, shared):
    """The one-sentence description of what is uploaded, by source kind."""
    fields = dict(target=target, title=title, shared=shared)
    if source == "OBJECTS":
        return plural_(target, "Upload {count} scene object as one GLB model as “{title}” · {shared}",
                       "Upload {count} scene objects as one GLB model as “{title}” · {shared}", title=title, shared=shared)
    if source == "STUDIO_ASSET":
        return iface_("Upload the Media Studio output {target} as “{title}” · {shared}", **fields)
    if source == "VIEW":
        return iface_("Upload the scene image (view {target}) as “{title}” · {shared}", **fields)
    return iface_("Upload the image attached to this chat ({target}) as “{title}” · {shared}", **fields)


def message(name, args):
    organization, org_id = _organization()
    if name == "angelo_team_library_import":
        facts = team_library_results.known(org_id, args["item_id"])
        into = args.get("into", "SCENE")
        fields = dict(item=facts.get("title") or iface_("item {id}", id=args["item_id"]),
                      file=facts.get("fileName") or facts.get("mimeType") or facts.get("kind") or iface_("file"),
                      size=_size(facts.get("sizeBytes")), organization=organization, person=facts.get("by"))
        team = facts.get("space", "team") != "personal"
        if facts.get("by"):
            body = (iface_("“{item}” · {file} · {size} · team space of {organization} · added by {person}", **fields) if team else
                    iface_("“{item}” · {file} · {size} · personal space of {organization} · added by {person}", **fields))
        else:
            body = (iface_("“{item}” · {file} · {size} · team space of {organization}", **fields) if team else
                    iface_("“{item}” · {file} · {size} · personal space of {organization}", **fields))
        if into == "STUDIO":
            return {"title": iface_("Copy a team-library image into Media Studio"), "body": body,
                    "warning": iface_("Downloads the file from your team's storage into the local Media Studio library. "
                                      "Library changes aren't reverted by Blender Undo.")}
        return {"title": iface_("Import from your team library"), "body": body,
                "warning": iface_("Downloads the file and imports it into the bound scene after checking it (format, size, "
                                  "embedded files). Nothing in it runs; Blender Undo removes the import.")}
    if name == "angelo_team_library_save":
        source = args["source"]
        target = (len(args["objects"]) if source == "OBJECTS" else
                  args.get("asset_id") or args.get("view_id") or args.get("reference_id"))
        shared = (iface_("everyone in {organization} will see it", organization=organization) if args["space"] == "team"
                  else iface_("only you will see it (your personal space in {organization})", organization=organization))
        body = _upload_line(source, target, args["title"], shared)
        if args.get("tags"):
            body += "\n" + iface_("Tags: {tags}", tags=", ".join(args["tags"]))
        if args.get("description"):
            body += "\n" + args["description"][:400] + ("…" if len(args["description"]) > 400 else "")
        if args.get("ip_entities"):
            body += "\n" + plural_(len(args["ip_entities"]), "Records {count} IP entity version it used.",
                                   "Records {count} IP entity versions it used.")
        return {"title": iface_("Save to your team library"), "body": body,
                "warning": iface_("Uploads a file to your team's library. Blender Undo can't reverse it; the uploader or a "
                                  "team admin can delete it in the Angelo console.")}
    return {"title": iface_("Team library action"), "body": name, "warning": ""}
