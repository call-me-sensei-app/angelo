# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every GPU-drawn entry point consults the one capability check, and nothing creates a GPU context outside it
(doc/angelo/render_engines.rst, "Machines without a GPU"). No Blender: the C++ sources are read.

On a machine without a usable GPU (Microsoft's GDI Generic 1.1 on Windows, a remote desktop, an outdated driver) the
system gives no GPU context, and the OpenGL backend reports support without probing. A GPU path that went on regardless
ended Angelo with an access violation (test_blender_look on the 2.1.0 Windows builder, 4235812eb92). The behaviour is
proven by test_blender_gpu_unavailable (a QA knob makes the context refuse on any machine); this test keeps the gate
in place as the code changes:

* ``WM_gpu_is_available`` answers by trying: on every thread, never the backend's unprobed "supported".
* each entry point below consults it (or the GPU module's own initialized state, below the window manager);
* every call that creates a GPU context, or enables the draw manager's or a render's, is one counted here, so a
  new one fails until it is gated and added (comments never count; definitions and declarations are not calls).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATE = "WM_gpu_is_available()"

# (file, function, tokens its body must hold, why)
ENTRY_POINTS = (
    ("source/blender/render/intern/engine.cc", "RE_engine_render", (GATE,), "EEVEE and Workbench renders"),
    ("source/blender/render/intern/engine.cc", "engine_render_view_layer", (GATE,),
     "grease pencil composited over another engine's render"),
    ("source/blender/editors/space_view3d/view3d_draw.cc", "ED_view3d_draw_offscreen_imbuf", (GATE,),
     "the off-screen 3D view behind previews, thumbnails and captures"),
    ("source/blender/editors/render/render_shading.cc", "lightprobe_cache_bake_invoke", (GATE,), "light probe bake"),
    ("source/blender/editors/render/render_shading.cc", "lightprobe_cache_bake_exec", (GATE,), "light probe bake"),
    ("source/blender/python/gpu/gpu_py_api.cc", "pygpu_init", (GATE, "GPU_is_init()"), "gpu.init()"),
    ("source/blender/render/intern/pipeline.cc", "RE_display_ensure_gpu_context", (GATE,),
     "a render job's and the compositor job's own context (render.render INVOKE_DEFAULT)"),
    ("source/blender/makesrna/intern/rna_image_api.cc", "rna_Image_gl_load", (GATE,), "Image.gl_load / gl_touch"),
    ("source/blender/imbuf/intern/module.cc", "IMB_ensure_gpu_context", ("GPU_is_init()",),
     "the image module's shared context"),
    ("source/blender/gpu/intern/gpu_context.cc", "GPU_create_secondary_context", ("GPU_is_init()",),
     "every secondary context (sequencer prefetch, image module, shader threads)"),
    ("source/blender/gpu/intern/gpu_framebuffer.cc", "GPU_offscreen_create", ("GPU_is_init()",),
     "any other caller's off-screen buffer"),
    ("source/blender/draw/intern/draw_gpu_context.cc", "DRW_render_context_enable", ("viewport_context == nullptr",),
     "an engine's render context once the start was refused"),
)

# Every call that creates a GPU context (a system one, a GPU module one, the draw manager's), and every call that
# enables the draw manager's context or a render's, by file: a new one fails until it is gated (or shown reached only
# through a gated entry point) and counted here. Comments never count; definitions and declarations are not calls.
CREATORS = ("WM_system_gpu_context_create", "GPU_create_secondary_context", "DRW_gpu_context_create",
            "createOffscreenContext", "GPU_context_create", "GHOST_CreateGPUContext", "GHOST_CreateOpenGLContext")
ENABLERS = ("DRW_gpu_context_enable", "DRW_render_context_enable")
SUFFIXES = (".c", ".cc", ".cpp", ".mm", ".h", ".hh")
KNOWN_CREATORS = {
    # DRW_gpu_context_create's two system contexts and ContextShared's GPU context: behind WM_init_gpu_offscreen.
    "source/blender/draw/intern/draw_gpu_context.cc": 3,
    "source/blender/windowmanager/intern/wm_init_exit.cc": 1,   # WM_init_gpu_offscreen, the gate's own start.
    # GPU_create_secondary_context's system and GPU contexts, and GPUSecondaryContext: behind its GPU_is_init check.
    "source/blender/gpu/intern/gpu_context.cc": 3,
    "source/blender/imbuf/intern/module.cc": 1,   # IMB_ensure_gpu_context, behind GPU_create_secondary_context's.
    "source/blender/sequencer/intern/prefetch.cc": 1,   # Behind the same.
    # RenderDisplay's system and GPU contexts: behind RE_display_ensure_gpu_context.
    "source/blender/render/intern/render_types.cc": 2,
    "source/blender/render/intern/engine.cc": 2,   # The viewport render: a window exists, so a GPU does.
    "source/blender/draw/engines/eevee/eevee_lightcache.cc": 2,   # Reached only through the gated bake operators.
    # The window manager's own: WM_system_gpu_context_create itself, and each window's GPU context (a window exists).
    "source/blender/windowmanager/intern/wm_window.cc": 2,
    "source/blender/windowmanager/intern/wm_playanim.cc": 1,   # The animation player (-a): a window of its own.
    # The GL backend's shader-compilation subprocess: started only by an initialized GPU module, in its own process.
    "source/blender/gpu/opengl/gl_compilation_subprocess.cc": 2,
}
KNOWN_ENABLERS = {
    # Each enables the draw manager's context or a render's: without a GPU context both do nothing
    # (draw_gpu_context.cc), and the GPU-drawn work behind them is gated above. Counted so a new caller is reviewed.
    "source/blender/draw/engines/eevee/eevee_instance.cc": 1,
    "source/blender/draw/engines/eevee/eevee_lightcache.cc": 2,
    "source/blender/draw/intern/draw_context.cc": 1,
    "source/blender/draw/intern/draw_gpu_context.cc": 1,
    "source/blender/draw/intern/draw_select_buffer.cc": 1,
    "source/blender/editors/render/render_opengl.cc": 4,
    "source/blender/editors/space_view3d/view3d_draw.cc": 1,
    "source/blender/editors/space_view3d/view3d_view.cc": 1,
    "source/blender/gpu/intern/gpu_viewport.cc": 1,
    "source/blender/render/intern/compositor.cc": 3,
    "source/blender/render/intern/engine.cc": 7,
    "source/blender/sequencer/intern/cache/compositor_cache.cc": 1,
    "source/blender/sequencer/intern/render.cc": 1,
}


def strip_comments(text):
    """C and C++ comments replaced by spaces (string literals kept), so a mention in a comment never counts."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(re.sub(r"[^\n]", " ", text[i:j])); i = j
        elif text[i] in "\"'":
            quote, j = text[i], i + 1
            while j < n and text[j] != quote:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1]); i = j + 1
        else:
            out.append(text[i]); i += 1
    return "".join(out)


def calls(text, names):
    """How many times ``text`` (comments stripped) calls one of ``names``: a definition (``{`` after the parameter
    list) and a declaration (a statement holding only a type before the name) are not calls."""
    count = 0
    for match in re.finditer(r"\b(%s)\s*\(" % "|".join(map(re.escape, names)), text):
        depth, j = 0, match.end() - 1
        while j < len(text):
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            if depth == 0:
                break
            j += 1
        after = text[j + 1:j + 200].lstrip()
        if after.startswith("{") or re.match(r"(const|override|noexcept|final)\b[^;{]*\{", after):
            continue   # A definition.
        statement = re.split(r"[;{}]", text[:match.start()])[-1]
        if re.fullmatch(r"\s*(?:[A-Za-z_][\w:<>]*[\s*&]+)+(?:[A-Za-z_]\w*::)*", statement) and not re.search(
                r"\b(return|if|while|for|else|case)\b", statement):
            continue   # A declaration: a return type, then the name.
        count += 1
    return count


def source_files():
    for folder in ("source/blender", "intern/ghost"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.suffix in SUFFIXES and "/tests/" not in path.as_posix() and not path.name.endswith("_test.cc"):
                yield path


def body(path, name):
    """The body of the function ``name`` defined at the start of a line in ``path`` (comments stripped first, so a
    mention in a comment never satisfies a check; braces matched)."""
    text = strip_comments((ROOT / path).read_text(encoding="utf-8"))
    match = re.search(r"^[^\n;#]*\b%s\s*\(" % re.escape(name), text, re.MULTILINE)
    if match is None:
        return None
    start = text.index("{", match.end())
    depth = 0
    for index in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[index], 0)
        if depth == 0:
            return text[start:index + 1]
    return None


class GpuCapabilityGate(unittest.TestCase):

    def test_the_gate_answers_by_trying(self):
        gate = body("source/blender/windowmanager/intern/wm_init_exit.cc", "WM_gpu_is_available")
        self.assertIsNotNone(gate)
        self.assertIn("WM_init_gpu_offscreen()", gate)
        # The OpenGL backend reports support without probing: an answer from it alone is a guess.
        self.assertNotIn("GPU_backend_supported", gate)
        self.assertNotIn("BLI_thread_is_main", gate, "the answer must be the system's on every thread")
        start = body("source/blender/windowmanager/intern/wm_init_exit.cc", "WM_init_gpu_offscreen")
        self.assertIn("gpu_init_mutex", start, "the on-demand start is serialized (a job thread may ask too)")

    def test_every_entry_point_consults_it(self):
        for path, name, tokens, why in ENTRY_POINTS:
            with self.subTest(function=name):
                found = body(path, name)
                self.assertIsNotNone(found, "%s: %s not found (renamed? update this list)" % (path, name))
                for token in tokens:
                    self.assertIn(token, found, "%s (%s) must consult %s" % (name, why, token))

    def _counts(self, names):
        seen = {}
        for path in source_files():
            count = calls(strip_comments(path.read_text(encoding="utf-8", errors="replace")), names)
            if count:
                seen[path.relative_to(ROOT).as_posix()] = count
        return seen

    def test_no_context_is_created_outside_the_known_callers(self):
        self.assertEqual(self._counts(CREATORS), KNOWN_CREATORS,
                         "a GPU context is created somewhere new: gate it with WM_gpu_is_available() (or the GPU "
                         "module's GPU_is_init() below the window manager) and count it in KNOWN_CREATORS")

    def test_no_context_is_enabled_outside_the_known_callers(self):
        self.assertEqual(self._counts(ENABLERS), KNOWN_ENABLERS,
                         "the draw manager's or a render's context is enabled somewhere new: make sure that path "
                         "consults WM_gpu_is_available() (or refuses on DRW_gpu_context_is_enabled()) and count it in "
                         "KNOWN_ENABLERS")

    def test_the_scanner_tells_calls_from_definitions_and_comments(self):
        sample = ("GHOST_IContext *WM_system_gpu_context_create();\n"            # a declaration
                  "bool DRW_gpu_context_create()\n{\n  return true;\n}\n"     # a definition
                  "// WM_system_gpu_context_create();\n"                        # a comment
                  "/* DRW_gpu_context_create(); */\n"
                  "static void f()\n{\n  DRW_gpu_context_create();\n"           # three calls
                  "  return WM_system_gpu_context_create();\n"
                  "  ctx = GPU_context_create(nullptr, system);\n}\n")
        self.assertEqual(calls(strip_comments(sample), CREATORS), 3)


if __name__ == "__main__":
    unittest.main()
