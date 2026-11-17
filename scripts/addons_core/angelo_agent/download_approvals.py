# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The approval card of ``angelo_download`` (doc/angelo/downloads.rst): the host in the title, the link, the kind, the
size cap and the folder in the body, and what Angelo does and doesn't do with the file. ``approvals.approval_message``
routes the tool here. Card text is translated where it is drawn (whole sentences, named placeholders); the link and
host are shown as given."""

from urllib.parse import urlsplit

import bpy

from .download_spec import MAX_BYTES
from .ui_text import iface_, n_

CHIP = n_("File download")   # The card's chip (approval_families.approval_kind): a download changes no scene.
TITLE = n_("Download a file from {host}")
KINDS = {"texture": n_("Texture"), "model": n_("3D model"), "hdri": n_("HDRI environment"), "image": n_("Image"),
         "audio": n_("Sound"), "other": n_("File")}
BODY_PROJECT = n_("{kind}, up to {size}. Saved in assets/downloads in this project's folder.")
BODY_UNSAVED = n_("{kind}, up to {size}. Saved in a temporary folder for this unsaved file. Once you save the file, "
                  "downloads go to assets/downloads beside it.")
PROXY_LOOKUP = n_("If this computer can't look up {host}, your network's proxy finds its address instead, and Angelo "
                  "can't check where it leads.")   # download_fetch.proxy_lookup_possible; downloads.rst.
WARNING = n_("Angelo checks the file's type from its contents and saves it. It isn't opened, run or imported.")
SHOWN_LINK = 160   # Characters of the link the card shows.


def message(name, args, usage_label=""):
    url = str(args.get("url", ""))
    host = urlsplit(url).hostname or "?"
    shown = url if len(url) <= SHOWN_LINK else url[:SHOWN_LINK - 1] + "…"
    body = BODY_PROJECT if bpy.data.filepath else BODY_UNSAVED
    fields = dict(kind=iface_(KINDS.get(args.get("kind"), KINDS["other"])), size="%d MB" % (MAX_BYTES // 1048576))
    from . import download_fetch
    body = iface_(body, **fields) + ("\n" + iface_(PROXY_LOOKUP, host=host)
                                     if download_fetch.proxy_lookup_possible(host) else "")
    return {"title": iface_(TITLE, host=host), "body": shown + "\n" + body, "warning": iface_(WARNING)}
