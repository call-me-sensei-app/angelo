# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web tools on the host (``web_export_spec``): the agent's and MCP apps' route to the export and preview.

EXPORT writes like a background job's output (``job_outputs``): inside the chat's file workspace, through held
folders that are never links, published under a free name and never over an existing file. Results carry
workspace-relative names and the loopback preview URL, never a local path. An export whose World's picture is still
being made returns a pending call (``WebExportPending``) that the controller polls on the main thread; Blender
stays responsive meanwhile, and a stopped turn discards the staged file.
"""

import uuid

from . import web_export, web_preview
from .refusal_codes import RefusalError, error_result
from .web_export_wait import WAIT_SECONDS


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
        pending = web_export.pending_export(window, window.scene, window.view_layer,
                                            str(publication.path / output.name), request, preflight=True)
        report = pending.step()
    except BaseException:
        publication.discard()
        raise
    if report is None:
        return _pending_class()(controller, pending, publication, output)
    return _publish(controller, publication, output, report)


def _publish(controller, publication, output, report):
    try:
        publication.publish_file(output.name)
        written, published = publication.relative_output(), str(publication.published)
    except BaseException as error:
        if not getattr(publication, "left_published", None):
            publication.discard()   # Nothing of it stays published: an export is made again from the scene.
            raise
        # Moved files it couldn't move back stay published (job_output_links): named, and the rest kept, never lost.
        from .job_manager import kept_outputs, unpublished_receipt
        kept = kept_outputs(publication)
        if kept is None:
            publication.discard()
        receipt = unpublished_receipt(publication, kept)
        raise RefusalError("%s (%s)" % (receipt.pop("note"), error), code="CLEANUP_FAILED",
                           details=receipt) from error
    publication.close()
    try:   # Report-only from here: the file is published, so a failure costs the preflight, never the export.
        _annotate(controller, report, published, written)
    except Exception as error:  # noqa: BLE001
        report.pop("_readback", None)
        report["preflight"] = {"status": "unavailable", "error": type(error).__name__}
    return dict(web_export.for_agent(report), output=written)


def _annotate(controller, report, published, written):
    """The published export's preflight: the background read-back's start and the rules grouped for the model."""
    readback = report.pop("_readback", None)
    if readback is not None:   # The read-back runs in the background; its result arrives with the next request.
        from . import web_readback
        state, revision = web_readback.start(controller, published, written, readback["source"],
                                             readback["merged"])
        report["preflight"].update(readback=state, readback_revision=revision, readback_note=(
            "A copy of the file is being read back and compared with the scene in the background; the result, with "
            "this readback_revision, arrives with the next message (observation preflight)." if state == "running"
            else "No read-back in this session."))
    if "preflight" in report:   # The rules grouped for the model (engine_findings), as angelo_engine_check sends them.
        from .engine_findings import summarize
        preflight = dict(report["preflight"])
        preflight.update(summarize(preflight.pop("findings")))
        report["preflight"] = preflight


def _pending_class():
    from .studio_agent import StudioPending

    class WebExportPending(StudioPending):
        """``angelo_web_export`` while its World's picture is being made (``web_export_wait``)."""

        status_text = "Making the World's picture for the web export"
        activity_note = "The World's picture is made in the background; the export is written when it lands."
        stopped_result = {"error_code": "CANCELLED", "error": "The web export was stopped while the World's picture "
                          "was being made; nothing was written.", "scene_changed": False}
        timeout = WAIT_SECONDS + 30   # Past the wait, the export is written without the picture first.

        def __init__(self, controller, waiting, publication, output):
            super().__init__(self._step, self._stop)
            self.controller, self.waiting, self.publication, self.output = controller, waiting, publication, output

        def _step(self):
            try:
                report = self.waiting.step()
                return None if report is None else _publish(self.controller, self.publication, self.output, report)
            except web_export.WebExportError as error:
                self.publication.discard()
                return error_result(RefusalError(str(error), code="VALIDATION"))
            except Exception as error:  # Answered as the call's result: a poll that raises reads as a provider's.
                self.publication.discard()
                return error_result(error)

        def _stop(self):
            self.waiting.cancel()
            self.publication.discard()

    return WebExportPending


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
