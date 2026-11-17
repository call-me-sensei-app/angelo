# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every place Blender's C and C++ open a file for writing, and what decides where it writes
(test_native_c_write_sinks; the re-read of 0a0d4e95743, MUST-3: "every call site that opens a file for writing with
a path from data maps to its declared inputs").

A site is a call of ``fopen``/``BLI_fopen`` with a writing mode (or a mode passed in), ``BLI_open`` with
``O_WRONLY``/``O_RDWR``/``O_CREAT``, ``IMB_save_image``, ``BKE_imbuf_write*``, ``avio_open``, ``cdf_write_open``,
``gzopen`` for writing, or a ``std::ofstream``/``fstream`` opened for output, keyed ``file::function::kind`` (no line
numbers). ``SITES`` says for each why its path is never one a script chose unjudged:

* ``JUDGED``: its function asks the writers' gate (``angelo_writers::refusal*``, an ``angelo_*refus*`` helper over it,
  an ``InputScope`` with its declared inputs) or the file gate (``gate->refused``) before the write. Checked.
* ``("caller", "file::function")``: the path is judged where that function, in the call chain, asks. Checked.
* Any other text: why its path is not from data a script sets (an operator's or function's own argument, which the
  operator judge and the file gate judge; the person's configuration; a cache; a debug dump; a build tool).
"""

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3] / "source/blender"

KINDS = {
    "fopen": re.compile(r'\b(?:BLI_)?fopen\s*\((?:[^,;()]|\([^()]*\))+,\s*(?:"[^"]*[wa+][^"]*"|[A-Za-z_]\w*\s*\))'),
    "BLI_open": re.compile(r'\bBLI_open\s*\([^;]*O_(?:WRONLY|RDWR|CREAT)'),
    "IMB_save_image": re.compile(r'\bIMB_save_image\s*\('),
    "BKE_imbuf_write": re.compile(r'\bBKE_imbuf_write(?:_as|_stamp)?\s*\('),
    "avio_open": re.compile(r'\bavio_open2?\s*\('),
    "cdf_write_open": re.compile(r'\bcdf_write_open\s*\('),
    "gzopen": re.compile(r'\b(?:BLI_)?gzopen\s*\((?:[^,;()]|\([^()]*\))+,\s*"[^"]*w'),
    "ofstream": re.compile(r'\bstd::ofstream\b|\bfstream\s+\w+\s*[({][^;]*ios::out'),
}
# A declaration or definition of the writer itself, not a call of it.
_DECLARATION = re.compile(r'^\s*(?!return\b)(?:[\w:<>]+[\s\*&]+)+\**(?:IMB_save_image|BKE_imbuf_write\w*|cdf_write_open|avio_open2?)'
                          r'\s*\(')
_FUNCTION = re.compile(r'^(?:\[\[\w+\]\]\s*)?(?:[A-Za-z_][\w:<>,\s\*&]*?)\b([A-Za-z_][\w:~]*)\s*\(')
_SKIP = ("/tests/", "_test.cc", "/extern/", "/gtests/")
_SUFFIXES = (".c", ".cc", ".cpp", ".h", ".hh", ".mm")
GATE = re.compile(r"angelo_\w*refus|angelo_writers::refusal|gate->refused|InputScope")

JUDGED = "judged here"
OPERATOR = "an operator's own path argument (the operator judge and the file gate judge it)"
ARGUMENT = "its caller's path argument (a helper: every caller with a path from data is a site of its own)"
PYTHON = "a Python function's argument (agent code's own path: the file gate judges it on a confined thread)"
CONFIG = "the person's configuration or Blender's own per-user file, at a fixed place"
CACHE = "a cache or temporary file in Blender's own cache or temporary folder"
DEBUG = "a debug dump to a fixed name, or only with a debug option or build flag"
TOOL = "a build tool or separate program, not part of the running app"
ANGELO = "Angelo's own state, at a fixed place under its folders (or made exclusively by C's judgement)"
PROJECT = "the person's open Blender project (its root, fixed by the person's own open), never data a script sets"

SITES = {
    # Writers of paths data names: judged where they write.
    "blenkernel/intern/bake_items_serialize.cc::DiskBlobWriter::write_as_stream::ofstream": JUDGED,
    "blenkernel/intern/bake_geometry_nodes_modifier_pack.cc::unpack_bake_to_disk::ofstream":
        ("caller", "blenkernel/intern/bake_geometry_nodes_modifier_pack.cc::unpack_geometry_nodes_bake"),
    "blenkernel/intern/customdata.cc::CustomData_external_write::cdf_write_open": JUDGED,
    "blenkernel/intern/dynamicpaint.cc::dynamicPaint_outputSurfaceImage::IMB_save_image": JUDGED,
    "blenkernel/intern/image_save.cc::image_save_single::BKE_imbuf_write": JUDGED,
    "blenkernel/intern/image_save.cc::image_render_write_stamp_test::BKE_imbuf_write": JUDGED,
    "blenkernel/intern/movieclip.cc::movieclip_build_proxy_ibuf::IMB_save_image": JUDGED,
    "blenkernel/intern/ocean.cc::ocean_bake_write::BKE_imbuf_write": JUDGED,
    "blenkernel/intern/packedFile.cc::BKE_packedfile_write_to_file::BLI_open": JUDGED,
    "blenkernel/intern/pointcache.cc::ptcache_file_open::fopen": JUDGED,
    "editors/object/object_bake_api.cc::write_external_bake_pixels::BKE_imbuf_write":
        ("caller", "editors/object/object_bake_api.cc::bake_targets_output_external"),
    "editors/object/object_bake_simulation.cc::write_meta_file::ofstream":
        ("caller", "editors/object/object_bake_simulation.cc::bake_geometry_nodes_startjob"),
    "editors/space_image/image_ops.cc::image_save_sequence_exec::IMB_save_image": JUDGED,
    "editors/space_text/text_ops.cc::txt_write_file::fopen": JUDGED,
    "imbuf/movie/intern/movie_proxy_indexer.cc::alloc_proxy_output_ffmpeg::avio_open": JUDGED,
    "imbuf/movie/intern/movie_write.cc::start_ffmpeg_impl::avio_open": JUDGED,
    "sequencer/intern/proxy.cc::seq_proxy_build_frame::IMB_save_image": JUDGED,
    # Helpers: the path is their caller's.
    "blenkernel/intern/customdata_file.cc::cdf_write_open::fopen": ARGUMENT,
    "blenkernel/intern/image.cc::BKE_imbuf_write::IMB_save_image": ARGUMENT,
    "blenkernel/intern/image.cc::BKE_imbuf_write_as::BKE_imbuf_write": ARGUMENT,
    "blenkernel/intern/image.cc::BKE_imbuf_write_stamp::BKE_imbuf_write": ARGUMENT,
    "blenkernel/intern/report.cc::BKE_report_write_file::fopen": ARGUMENT,
    "blenlib/intern/fileops_c.cc::BLI_file_touch::fopen": ARGUMENT,
    "blenlib/intern/fileops_c.cc::BLI_fopen::fopen": ARGUMENT,
    "blenlib/intern/fileops_c.cc::copy_single_file::fopen": ARGUMENT,
    "blenlib/intern/serialize.cc::write_json_file::ofstream": ARGUMENT,
    "imbuf/intern/cineon/cineonlib.cc::cineonCreate::fopen": ARGUMENT,
    "imbuf/intern/format_jpeg.cc::save_stdjpeg::fopen": ARGUMENT,
    "imbuf/intern/openexr/openexr_api.cpp::?::ofstream": ARGUMENT,
    "io/ply/exporter/ply_file_buffer.cc::FileBuffer::fopen": ARGUMENT,
    "io/stl/exporter/stl_export_writer.cc::FileWriter::fopen": ARGUMENT,
    "io/wavefront_obj/exporter/obj_export_file_writer.cc::OBJWriter::fopen": ARGUMENT,
    "io/wavefront_obj/exporter/obj_export_file_writer.cc::MTLWriter::fopen": ARGUMENT,
    "io/alembic/exporter/abc_archive.cc::create_archive::ofstream": ARGUMENT,
    "io/alembic/exporter/abc_archive.h::?::ofstream": ARGUMENT,
    # Operators and functions whose path is their own argument.
    "editors/screen/screendump.cc::screenshot_exec::BKE_imbuf_write": OPERATOR,
    "editors/space_sequencer/sequencer_edit.cc::sequencer_export_subtitles_exec::fopen": OPERATOR,
    "io/usd/intern/usd_capi_export.cc::cache_image_color::IMB_save_image": OPERATOR,
    "io/usd/intern/usd_writer_material.cc::export_in_memory_imbuf::BKE_imbuf_write": OPERATOR,
    "blenkernel/intern/studiolight.cc::studiolight_write_solid_light::fopen": OPERATOR,
    "makesrna/intern/rna_depsgraph.cc::rna_Depsgraph_debug_relations_graphviz::fopen": PYTHON,
    "makesrna/intern/rna_depsgraph.cc::rna_Depsgraph_debug_stats_gnuplot::fopen": PYTHON,
    "python/generic/imbuf_py_api.cc::imbuf_write_impl::IMB_save_image": PYTHON,
    # The person's configuration, caches, projects.
    "blenkernel/intern/recents.cc::save::fopen": CONFIG,
    "editors/interface/interface_string_search.cc::write_recent_searches_file::ofstream": CONFIG,
    "editors/space_file/fsmenu.cc::fsmenu_write_file::fopen": CONFIG,
    "windowmanager/intern/wm_files.cc::wm_history_file_write::fopen": CONFIG,
    "windowmanager/intern/wm_platform_support.cc::wm_platform_support_check_approval::fopen": CONFIG,
    "editors/asset/intern/asset_indexer.cc::init_indexer_entries_from_value::ofstream": CACHE,
    "imbuf/intern/thumbs.cc::thumb_create_ex::IMB_save_image": CACHE,
    "gpu/opengl/gl_compilation_subprocess.cc::GPU_compilation_subprocess_run::ofstream": CACHE,
    "gpu/vulkan/vk_pipeline_pool.cc::VKPipelinePool::write_to_disk::ofstream": CACHE,
    "editors/space_project/project_splash_ops.cc::project_splash_image_create_exec::IMB_save_image": PROJECT,
    "blenkernel/intern/blender_project_config.cc::BKE_blender_project_config_file_write::ofstream": PROJECT,
    # Angelo's own.
    "python/intern/bpy_angelo_autorun_trust.cc::add::fopen": ANGELO,
    "python/intern/bpy_angelo_clipboard.cc::write_file::fopen": ANGELO,
    "python/intern/bpy_angelo_guard_confine_beside.cc::make_below::fopen": ANGELO,
    "python/intern/bpy_angelo_guard_confine_paths.cc::open_ungated::fopen": ANGELO,
    "python/intern/bpy_angelo_guard_confine_writable.cc::guard_create_beside::fopen": ANGELO,
    "windowmanager/intern/wm_files.cc::WM_angelo_recovery_note_write::fopen": ANGELO,
    # Debug dumps and build tools.
    "blenkernel/intern/dynamicpaint.cc::dynamicPaint_createUVSurface::fopen": DEBUG,
    "blenlib/intern/delaunay_2d.cc::cdt_draw::ofstream": DEBUG,
    "blenlib/intern/mesh_boolean.cc::write_obj_cell_patch::ofstream": DEBUG,
    "blenlib/intern/mesh_intersect.cc::write_obj_mesh::ofstream": DEBUG,
    "blenlib/intern/scanfill_utils.cc::BLI_scanfill_obj_dump::fopen": DEBUG,
    "blenloader/intern/writefile.cc::BLO_write_file_impl::ofstream": DEBUG,
    "draw/engines/eevee/eevee_precompute.hh::?::ofstream": DEBUG,
    "freestyle/intern/stroke/Canvas.cpp::Canvas::loadMap::IMB_save_image": DEBUG,
    "freestyle/intern/view_map/SteerableViewMap.cpp::SteerableViewMap::saveSteerableViewMap::IMB_save_image": DEBUG,
    "geometry/intern/mesh_boolean.cc::write_boolean_benchmark_time::ofstream": DEBUG,
    "gpu/intern/gpu_shader.cc::Shader::dump_source_to_disk::ofstream": DEBUG,
    "gpu/vulkan/vk_shader_compiler.cc::write_spirv_to_disk::ofstream": DEBUG,
    "io/fbx/importer/fbx_import.cc::?::fopen": DEBUG,
    "blendthumb/src/blender_thumbnailer.cc::extract_png_from_blend_file::BLI_open": TOOL,
    "blentranslation/msgfmt/msgfmt.cc::make::fopen": TOOL,
    "datatoc/datatoc.cc::main::fopen": TOOL,
    "gpu/shader_tool/shader_tool.cc::main::ofstream": TOOL,
    "makesdna/intern/makesdna.cc::make_bad_file::fopen": TOOL,
    "makesdna/intern/makesdna.cc::main::fopen": TOOL,
    "makesrna/intern/makesrna.cc::make_bad_file::fopen": TOOL,
    "makesrna/intern/makesrna.cc::rna_preprocess::fopen": TOOL,
}


def _function_start(lines, index):
    """The line where the function holding ``lines[index]`` starts, and its name (``?`` inside a class body)."""
    for start in range(index, -1, -1):
        line = lines[start]
        if line and not line[0].isspace() and line[0] not in "#}/*{" and "(" in line and \
                not line.rstrip().endswith(";"):
            found = _FUNCTION.match(line)
            if found:
                return start, found.group(1)
    return index, "?"


def function_body(relative, name):
    """The text of the function ``name`` (its last part) in ``relative``, or ``None``."""
    lines = (SOURCE / relative).read_text(errors="replace").splitlines()
    short = name.split("::")[-1]
    for start, line in enumerate(lines):
        found = _FUNCTION.match(line) if line and not line[0].isspace() else None
        if found and found.group(1).split("::")[-1] == short and not line.rstrip().endswith(";"):
            end = start
            while end < len(lines) and not lines[end].startswith("}"):
                end += 1
            return "\n".join(lines[start:end + 1])
    return None


def scan():
    """``{key: [(relative path, line number), ...]}`` for every site under ``source/blender``."""
    found = {}
    for path in sorted(SOURCE.rglob("*")):
        relative = path.relative_to(SOURCE).as_posix()   # Windows too: the skip list and keys use "/".
        if path.suffix not in _SUFFIXES or any(part in "/" + relative for part in _SKIP):
            continue
        text = path.read_text(errors="replace")
        lines = text.splitlines()
        for kind, pattern in KINDS.items():
            for match in pattern.finditer(text):
                index = text.count("\n", 0, match.start())
                line = lines[index]
                if line.lstrip().startswith(("*", "//", "/*")) or _DECLARATION.match(line):
                    continue
                start, name = _function_start(lines, index)
                body = "\n".join(lines[start:index + 1])
                found.setdefault("::".join((relative, name, kind)), []).append((relative, index + 1, body))
    return found
