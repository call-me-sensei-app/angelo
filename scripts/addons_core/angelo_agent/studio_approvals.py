# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Human-readable Studio approvals, with explicit account billing disclosure.

Card text is translated where it is drawn; prompts, options and account labels
are the person's or the provider's own words and fill placeholders untranslated.
"""

import json

from .ui_text import iface_, plural_


def message(name, args, account=""):
    action = name.removeprefix("angelo_studio_")
    if action == "generate":
        body = account or iface_("Account {account} · {model}", account=args["connection_id"], model=args["model"])
        body += "\n" + plural_(len(args.get("references", [])), "Upload {count} image reference. One output requested.",
                               "Upload {count} image references. One output requested.") + "\n"
        prompt = args["prompt"][:600]
        if len(args["prompt"]) > 600:
            prompt = iface_("{prompt}… Open Details for the full prompt.", prompt=prompt)
        options = args.get("options", {})  # Settings as "Name: value" rows, never raw JSON (Details keep the exact call).
        body += "".join(iface_("{label}: {value}", label=str(key).replace("_", " ").capitalize(), value=value) + "\n"
                        for key, value in options.items()) + prompt
        # The actual resolved mode is encoded in the account/model label; the
        # pinned Meshy models are exact wire IDs, not a inferred billing route.
        if args["model"] in {"meshy-7", "meshy-7.1"} and not args.get("references") and args.get("options", {}).get("texture", True):
            body += "\n" + iface_("Two billable stages: geometry preview + textured refine.")
        return {"title": iface_("Generate media with API credits"), "body": body,
            "warning": iface_("Billed to this API account, NOT your coding subscription. Nothing is inserted into the scene automatically. "
                              "Stop only ends local monitoring; remote processing/billing may continue. No automatic retries.")}
    if action == "enhance":
        return {"title": iface_("Enhance prompt"), "body": (account or iface_("Configured AI Enhance account")) + "\n" + args["prompt"][:600],
            "warning": iface_("Uses this account's API credits or subscription allowance. Text only; original unchanged. "
                              "Review the suggestion before generating.")}
    titles = {"create_folder": iface_("Create library folder “{name}”", name=args.get("name", "")),
        "move_asset": iface_("Move library asset"), "import_image": iface_("Import attached image into the library"),
        "import_3d": iface_("Import library model into the scene"),
        "export": iface_("Export library output to {path}", path=args.get("path", "")), "stop": iface_("Stop local generation monitoring")}
    warning = (iface_("Native scene import. Blender Undo is available.") if action == "import_3d" else
        iface_("Provider work may continue and be billed. This is not a guaranteed remote cancellation.") if action == "stop" else
        iface_("Library and exported files are not reverted by Blender Undo or scene-snapshot restore. "
               "Existing output files are never overwritten."))
    return {"title": titles.get(action) or iface_("Media Studio action"), "body": json.dumps(args, ensure_ascii=False), "warning": warning}
