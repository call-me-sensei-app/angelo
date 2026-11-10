# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``Report.message_untranslated``: the window manager's reports in English when the interface is not, in the app.

Blender translates a report as it creates it (``RPT_`` in ``BKE_report``/``BKE_reportf``, main thread only), so
``WindowManager.reports[...].message`` is in the person's language. The agent reads a failed job's reason from that list
and needs text that does not change with the locale (it is also part of a prompt). Pinned here, in Japanese:

* a plain report (``AnimData.drivers.from_existing(None)`` reports its message ID straight into the window manager's
  list): ``message`` is translated, ``message_untranslated`` is the message ID;
* a ``%s`` report (``wm_file_read_report``: a file whose scene names a render engine that is not registered): the
  untranslated text is the message ID's format filled with the same arguments, in English, never the translated format
  with English words;
* text translated before it reached the report call (a Python render engine raising a translated error, reported as
  ``"%s: %.*s"``): ``message_untranslated`` stays empty rather than holding mixed text;
* with report translation off nothing is stored (the field is empty and ``message`` is English).

Run headless: ``run_native_suites.py --include test_blender_report_untranslated.py``; check
``report-untranslated.json`` for ``success``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import contextlib
import io
import json
import tempfile
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-report-untranslated"))
OUT.mkdir(parents=True, exist_ok=True)
LANGUAGE = "ja_JP"
# Message IDs Blender's own code reports, each translated in the Japanese catalogue (checked below, not assumed).
PLAIN = "No valid driver data to create copy of"
ENGINE_FORMAT = "Engine '%s' not available for scene '%s' (an add-on may need to be installed or enabled)"
ENGINE = "ANGELO_REPORT_PROBE"

report = {"checks": {}, "details": {}}


def check(name, condition, **details):
    report["checks"][name] = bool(condition)
    if details:
        report["details"][name] = details
    assert condition, "%s: %r" % (name, details)


class ProbeEngine(bpy.types.RenderEngine):
    bl_idname = ENGINE
    bl_label = "Report Probe"
    raise_text = ""
    expected_tracebacks = []

    def render(self, depsgraph):
        try:
            raise RuntimeError(ProbeEngine.raise_text)
        except RuntimeError as error:
            ProbeEngine.expected_tracebacks.append(''.join(traceback.format_exception(error)))
            raise


def render_error(label):
    """Retain and verify only this deliberately injected Python traceback, never suppress other errors."""
    ProbeEngine.expected_tracebacks = []
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            try:
                bpy.ops.render.render()
            except RuntimeError:
                pass  # The operator may also report this expected render failure.
    finally:
        (OUT / (label + '-stderr.txt')).write_text(stderr.getvalue(), encoding='utf-8')
    expected = ProbeEngine.expected_tracebacks
    check(label + '_one_injected_exception', len(expected) == 1)
    check(label + '_only_expected_stderr', stderr.getvalue() == expected[0])


def newest():
    """The newest entry of the window manager's report list, as (type, message, message_untranslated)."""
    reports = bpy.context.window_manager.reports
    entry = reports[len(reports) - 1]
    return entry.type, entry.message, entry.message_untranslated


def language(code, translate_reports):
    view = bpy.context.preferences.view
    view.language = code
    view.use_translate_reports = translate_reports


def plain_report():
    obj = bpy.context.scene.objects[0]
    obj.animation_data_create()
    count = len(bpy.context.window_manager.reports)
    obj.animation_data.drivers.from_existing(src_driver=None)
    check("plain_report_reaches_the_window_manager", len(bpy.context.window_manager.reports) == count + 1)
    return newest()


def main():
    bpy.context.preferences.use_preferences_save = False
    check("the_field_is_declared", "message_untranslated" in bpy.types.Report.bl_rna.properties)
    language(LANGUAGE, True)
    translated_plain = bpy.app.translations.pgettext_rpt(PLAIN)
    translated_format = bpy.app.translations.pgettext_rpt(ENGINE_FORMAT)
    check("the_catalogue_translates_both_message_ids", translated_plain != PLAIN and translated_format != ENGINE_FORMAT,
          plain=translated_plain, format=translated_format)

    kind, message, untranslated = plain_report()
    check("plain_message_is_translated", kind == "ERROR" and message == translated_plain, message=message)
    check("plain_untranslated_is_the_message_id", untranslated == PLAIN, untranslated=untranslated)

    # A %s report: save a file whose scene names an engine, unregister the engine, open the file.
    scene = bpy.context.scene
    bpy.utils.register_class(ProbeEngine)
    engine_before = scene.render.engine
    scene.render.engine = ENGINE
    path = str(OUT / "engine-probe.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
    scene.render.engine = engine_before
    bpy.utils.unregister_class(ProbeEngine)
    bpy.ops.wm.open_mainfile(filepath=path)
    scene_name = bpy.context.scene.name
    expected_english = ENGINE_FORMAT % (ENGINE, scene_name)
    expected_translated = translated_format % (ENGINE, scene_name)
    found = [(r.type, r.message, r.message_untranslated) for r in bpy.context.window_manager.reports
             if ENGINE in r.message]
    check("format_report_reaches_the_window_manager", len(found) == 1, found=found)
    kind, message, untranslated = found[0]
    check("format_message_is_translated", kind == "ERROR" and message == expected_translated, message=message)
    check("format_untranslated_is_the_message_id_filled_in_english", untranslated == expected_english,
          untranslated=untranslated)

    # Text translated before the report call: a Python engine raises a translated error; Blender reports it as
    # "%s: %.*s" into the window manager's list (bpy_class_call, BPy_errors_to_report).
    ProbeEngine.raise_text = translated_plain
    bpy.utils.register_class(ProbeEngine)
    bpy.context.scene.render.engine = ENGINE
    count = len(bpy.context.window_manager.reports)
    render_error("translated")
    found = [(r.type, r.message, r.message_untranslated) for r in list(bpy.context.window_manager.reports)[count:]
             if translated_plain in r.message]
    check("call_site_translated_report_reaches_the_window_manager", len(found) >= 1, found=found)
    check("call_site_translated_report_keeps_the_field_empty", all(item[2] == "" for item in found), found=found)
    # English Python text is equally opaque: the wrapper cannot prove source-language provenance.
    ProbeEngine.raise_text = "English exception with opaque caller provenance"
    count = len(bpy.context.window_manager.reports)
    render_error("english")
    found = [(r.type, r.message, r.message_untranslated) for r in list(bpy.context.window_manager.reports)[count:]
             if ProbeEngine.raise_text in r.message]
    check("english_exception_display_is_preserved", len(found) >= 1, found=found)
    check("english_exception_has_no_invented_source", all(item[2] == "" for item in found), found=found)
    bpy.context.scene.render.engine = "BLENDER_WORKBENCH"
    bpy.utils.unregister_class(ProbeEngine)

    # Report translation off: nothing is stored, the message is English.
    language(LANGUAGE, False)
    kind, message, untranslated = plain_report()
    check("translation_off_message_is_english", message == PLAIN, message=message)
    check("translation_off_stores_nothing", untranslated == "", untranslated=untranslated)
    language("en_US", True)
    kind, message, untranslated = plain_report()
    check("english_interface_stores_nothing", message == PLAIN and untranslated == "", message=message,
          untranslated=untranslated)
    report["success"] = True


try:
    main()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    try:
        language("en_US", True)
    except Exception:
        pass
(OUT / "report-untranslated.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print("ANGELO_REPORT_UNTRANSLATED", json.dumps({"success": report["success"], "checks": report["checks"]}, ensure_ascii=False), flush=True)
if not report.get("success"):
    raise RuntimeError(report["error"])
