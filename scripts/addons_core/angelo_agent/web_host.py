# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web tools on the host (``web_export_spec``): the agent's and MCP apps' route to the export and preview.

EXPORT writes like a background job's output (``job_outputs``): inside the chat's file workspace, through held
folders that are never links, published under a free name and never over an existing file. Results carry
workspace-relative names and the loopback preview URL, never a local path.
"""

import uuid

from . import web_export, web_preview
from .refusal_codes import RefusalError


def _workspace(controller):
    files = getattr(controller, "files", None)
    if files is None or getattr(files, "root", None) is None:
        raise RefusalError("This conversation has no file workspace to export into; save the .blend file first",
                           code="UNSUPPORTED")
    return files


def _export(controller, window, request):
    from .job_outputs import Staging, admit_output
    files = _workspace(controller)
    output = admit_output(files, request["output"])
    publication = Staging(files.root, output, uuid.uuid4().hex[:8])
    publication.create()
    try:
        report = web_export.export(window, window.scene, window.view_layer, str(publication.path / output.name),
                                   request)
        publication.publish_file(output.name)
        written = publication.relative_output()
    except BaseException:
        publication.discard()
        raise
    publication.close()
    return dict(web_export.for_agent(report), output=written)


def execute(controller, window, name, request):
    from .web_export_spec import CHECK, EXPORT
    try:
        if name == CHECK:
            return web_export.for_agent(web_export.check(window, window.scene, window.view_layer, request))
        if name == EXPORT:
            return _export(controller, window, request)
        if request["action"] == "STOP":
            return {"stopped": web_preview.stop()}
        started = web_preview.start(window, {"meshes": "AUTO", "textures": "AUTO", "merge": True,
                                             "selection_only": False})
        report = started["report"]
        answer = {"url": started["url"], "live": True}
        answer.update(web_export.for_agent(report) if "findings" in report else {"status": report.get("status")})
        return answer
    except web_export.WebExportError as error:
        raise RefusalError(str(error), code="VALIDATION") from None
