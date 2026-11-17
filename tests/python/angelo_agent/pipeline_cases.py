# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pipeline scripts for test_blender_confinement_pipeline (doc/angelo/script_confinement_pipeline.rst).

Every script runs confined with ``PLAIN`` (an ordinary project folder), ``PRIV`` (a folder inside Angelo's state
folder), ``KEY`` (a private key file) and ``STARTUP`` (a folder Blender loads code from) bound.

* ``ORDINARY``: what a pipeline script does with its own files; each sets ``result = "ok"`` after its own checks.
* ``REFUSED``: each names a private (or startup) path directly and must be refused.
* ``SEALED``: each reaches a private file indirectly (composition, XIncludes, LUT search paths, packaging); it may
  fail or succeed, but nothing it returns or raises may hold ``SECRETMARK`` or the key, and nothing is written there.

The private fixtures are text the harness writes before any script (``text_fixtures``) and copies of what the
ordinary scripts wrote (``COPIED``), so no library is imported unconfined except those ``PRELOADED`` on purpose.
"""

import os

MARK = "SECRETMARK"
KEY_TEXT = "sk-original"
PRELOADED = ("pxr.Usd", "OpenImageIO")   # Imported by the harness first, as if the person used them earlier.

OSO = """OpenShadingLanguage 1.00
# Compiled by oslc 1.15.6
# options:
shader {name}
param\tfloat\t{param}\t1\t\t%read{{2147483647,-1}} %write{{2147483647,-1}}
code ___main___
\tend
"""


def text_fixtures(plain, priv, key):
    """name -> text, in PRIV (``priv/...``) or PLAIN (``plain/...``). Absolute asset paths take USD's ``@@@``
    delimiters: a run folder may hold an ``@`` (a suite variant's ``<suite>@<variant>``)."""
    rel = os.path.relpath(os.path.join(priv, "secret.usda"), plain)
    usda = '#usda 1.0\n(\n    subLayers = [@@@{}@@@]\n)\ndef Xform "Outer" {{}}\n'
    mtlx = ('<?xml version="1.0"?>\n<materialx version="1.39">\n  <nodedef name="ND_{0}" node="{0}">\n'
            '    <output name="out" type="float" />\n  </nodedef>\n</materialx>\n')
    include = '<?xml version="1.0"?>\n<materialx version="1.39">\n  <xi:include href="{}" />\n</materialx>\n'
    return {
        "priv/secret.usda": '#usda 1.0\ndef Xform "%s_PRIM" {}\n' % MARK,
        "priv/secret.mtlx": mtlx.format(MARK),
        "priv/secret.cube": 'TITLE "%s"\nLUT_1D_SIZE 2\n0.9 0.1 0.3\n0.9 0.1 0.3\n' % MARK,
        "priv/secret.ocio": ("ocio_profile_version: 2\ndescription: %s\nsearch_path: .\nroles:\n  default: raw\n"
                             "displays: {}\ncolorspaces:\n  - !<ColorSpace>\n    name: raw\n    isdata: true\n"
                             % MARK),
        "priv/secret.oso": OSO.format(name="secretshader", param=MARK),
        "priv/secret.otio": ('{"OTIO_SCHEMA": "Timeline.1", "metadata": {}, "name": "%s", "global_start_time": '
                             'null, "tracks": {"OTIO_SCHEMA": "Stack.1", "metadata": {}, "name": "tracks", '
                             '"source_range": null, "effects": [], "markers": [], "enabled": true, "children": []}}'
                             % MARK),
        "plain/outer_sub.usda": usda.format(os.path.join(priv, "secret.usda")),
        "plain/outer_rel.usda": usda.format(rel),
        "plain/outer_link.usda": usda.format("./link.usda"),
        "plain/outer_ref.usda": ('#usda 1.0\ndef Xform "Outer" (\n    prepend references = @@@%s@@@</%s_PRIM>\n)\n{}\n'
                                 % (os.path.join(priv, "secret.usda"), MARK)),
        "plain/outer_payload.usda": ('#usda 1.0\ndef Xform "Outer" (\n    prepend payload = @@@%s@@@</%s_PRIM>\n)\n{}\n'
                                     % (os.path.join(priv, "secret.usda"), MARK)),
        "plain/outer_mtlx.usda": ('#usda 1.0\ndef "Mtlx" (\n    prepend references = @@@%s@@@</MaterialX>\n)\n{}\n'
                                  % os.path.join(priv, "secret.mtlx")),
        "plain/outer_mtlx_include.usda": ('#usda 1.0\ndef "Mtlx" (\n    prepend references = @./inc_private.mtlx@'
                                          '</MaterialX>\n)\n{}\n'),
        "plain/inc_private.mtlx": include.format(os.path.join(priv, "secret.mtlx")),
        "plain/plain_lib.mtlx": mtlx.format("plainlib"),
        "plain/inc_plain.mtlx": include.format("plain_lib.mtlx"),
        "plain/tex_stage.usda": ('#usda 1.0\ndef Shader "Tex"\n{\n    asset inputs:file = @@@%s@@@\n}\n' % key),
        "plain/plain.cube": 'TITLE "plain"\nLUT_1D_SIZE 2\n0 0 0\n0.5 0.5 0.5\n',
        "plain/plain.oso": OSO.format(name="plainshader", param="plainparam"),
    }


# What the ordinary scripts wrote, copied into PRIV as binary fixtures after they ran.
COPIED = {"oiio_a.exr": "secret.exr", "oiio_b.png": "secret.png", "exr_a.exr": "secret_exr.exr",
          "vdb_a.vdb": "secret.vdb", "aud_a.wav": "secret.wav", "manta_a.uni": "secret.uni"}

ORDINARY = {
    # C code that imports a module (numpy arrays from pybind, `time.strptime`) works in a confined script.
    "c_level_import": """
import time
assert time.strptime("2020", "%Y").tm_year == 2020
result = "ok"
""",
    "usd": """
import os
from pxr import Usd, Sdf, UsdGeom, UsdUtils
p = lambda name: os.path.join(PLAIN, name)
stage = Usd.Stage.CreateNew(p("usd_new.usda"))
UsdGeom.Xform.Define(stage, "/Base")
stage.GetRootLayer().Save()
top = Usd.Stage.CreateNew(p("usd_top.usda"))
top.GetRootLayer().subLayerPaths.append("./usd_new.usda")
top.DefinePrim("/Ref").GetReferences().AddReference("./usd_new.usda", "/Base")
top.GetRootLayer().Save()
opened = Usd.Stage.Open(p("usd_top.usda"))
paths = sorted(str(prim.GetPath()) for prim in opened.Traverse())
opened.Export(p("usd_flat.usdc"))
flat = Sdf.Layer.FindOrOpen(p("usd_flat.usdc"))
packed = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(p("usd_top.usda")), p("usd_top.usdz"))
names = Sdf.ZipFile.Open(p("usd_top.usdz")).GetFileNames()
assert paths == ["/Base", "/Ref"], paths
assert flat is not None and packed and len(names) == 2, (flat, packed, names)
result = "ok"
""",
    "usd_operators": """
import os
bpy.ops.wm.usd_import(filepath=os.path.join(PLAIN, "usd_top.usda"))
assert "Base" in bpy.data.objects, list(bpy.data.objects.keys())
bpy.ops.wm.usd_export(filepath=os.path.join(PLAIN, "usd_operator.usdc"))
assert os.path.exists(os.path.join(PLAIN, "usd_operator.usdc"))
result = "ok"
""",
    "oiio": """
import os, OpenImageIO as oiio
p = lambda name: os.path.join(PLAIN, name)
buf = oiio.ImageBuf(oiio.ImageSpec(4, 4, 3, "float"))
oiio.ImageBufAlgo.fill(buf, (0.25, 0.5, 0.75))
assert buf.write(p("oiio_a.exr")), oiio.geterror()
image = oiio.ImageInput.open(p("oiio_a.exr"))
pixels = image.read_image()
image.close()
back = oiio.ImageBuf(p("oiio_a.exr"))
assert back.spec().width == 4 and pixels.shape == (4, 4, 3)
out = oiio.ImageOutput.create(p("oiio_b.png"))
assert out.open(p("oiio_b.png"), oiio.ImageSpec(4, 4, 3, "uint8"))
out.write_image(pixels)
out.close()
cache = oiio.ImageCache(False)
cache.attribute("searchpath", PLAIN)
assert cache.get_imagespec("oiio_a.exr").width == 4
result = "ok"
""",
    "openexr": """
import os, numpy, OpenEXR
path = os.path.join(PLAIN, "exr_a.exr")
OpenEXR.File({}, {"R": numpy.zeros((4, 4), "float32")}).write(path)
assert "R" in OpenEXR.File(path).channels()
result = "ok"
""",
    "openvdb": """
import os, openvdb
grid = openvdb.FloatGrid()
grid.name = "plainmark"
grid.getAccessor().setValueOn((0, 0, 0), 1.0)
path = os.path.join(PLAIN, "vdb_a.vdb")
openvdb.write(path, [grid])
grids, meta = openvdb.readAll(path)
assert grids[0].name == "plainmark" and openvdb.read(path, "plainmark") is not None
result = "ok"
""",
    "materialx": """
import os, MaterialX as mx
p = lambda name: os.path.join(PLAIN, name)
doc = mx.createDocument()
doc.addNodeDef("ND_plainmark", "float", "plainmark")
mx.writeToXmlFile(doc, p("mx_a.mtlx"))
back = mx.createDocument(); mx.readFromXmlFile(back, p("mx_a.mtlx"))
included = mx.createDocument(); mx.readFromXmlFile(included, p("inc_plain.mtlx"))
text = mx.createDocument()
mx.readFromXmlString(text, '<materialx version="1.39"><xi:include href="plain_lib.mtlx"/></materialx>',
                     mx.FileSearchPath(PLAIN))
assert back.getNodeDef("ND_plainmark") and included.getNodeDef("ND_plainlib") and text.getNodeDef("ND_plainlib")
assert "plainmark" in mx.readFile(p("mx_a.mtlx"))
result = "ok"
""",
    "ocio": """
import os, PyOpenColorIO as ocio
path = os.path.join(PLAIN, "c.ocio")
ocio.Config.CreateRaw().serialize(path)
config = ocio.Config.CreateFromFile(path)
lut = ocio.FileTransform(src=os.path.join(PLAIN, "plain.cube"), interpolation=ocio.INTERP_LINEAR)
rgb = config.getProcessor(lut).getDefaultCPUProcessor().applyRGB([1.0, 1.0, 1.0])
searched = ocio.Config.CreateRaw()
searched.setSearchPath(PLAIN)
again = searched.getProcessor(ocio.FileTransform(src="plain.cube")).getDefaultCPUProcessor().applyRGB([1.0] * 3)
assert abs(rgb[0] - 0.5) < 1e-3 and abs(again[0] - 0.5) < 1e-3, (rgb, again)
result = "ok"
""",
    "otio": """
import os, opentimelineio as otio
timeline = otio.schema.Timeline(name="plainmark")
otio.adapters.write_to_file(timeline, os.path.join(PLAIN, "otio_a.otio"))
assert otio.adapters.read_from_file(os.path.join(PLAIN, "otio_a.otio")).name == "plainmark"
timeline.to_json_file(os.path.join(PLAIN, "otio_b.otio"))
assert otio.schema.Timeline.from_json_file(os.path.join(PLAIN, "otio_b.otio")).name == "plainmark"
result = "ok"
""",
    "oslquery": """
import os, oslquery
query = oslquery.OSLQuery(os.path.join(PLAIN, "plain.oso"))
searched = oslquery.OSLQuery()
assert searched.open("plain", PLAIN)
result = "ok"
""",
    "aud": """
import os, aud
path = os.path.join(PLAIN, "aud_a.wav")
aud.Sound.sine(440).limit(0, 0.05).write(path, rate=44100, channels=aud.CHANNELS_MONO, format=aud.FORMAT_S16,
                                          container=aud.CONTAINER_WAV, codec=aud.CODEC_PCM)
sound = aud.Sound(path)
assert aud.Sound.file(path).length > 0 and sound.length > 0
result = "ok"
""",
    "manta": """
import os, manta
solver = manta.Solver(name="pipeline", gridSize=manta.vec3(8, 8, 8), dim=3)
grid = solver.create(manta.RealGrid)
path = os.path.join(PLAIN, "manta_a.uni")
grid.save(path)
grid.load(path)
del grid, solver   # Mantaflow's solver must outlive its grids (its destructor throws otherwise).
result = "ok"
""",
    "importlib": """
import importlib, os
openvdb = importlib.import_module("openvdb")
assert openvdb.readAll(os.path.join(PLAIN, "vdb_a.vdb"))[0]
result = "ok"
""",
}

# One statement each, run with ``os`` and the folder names bound.
REFUSED = {
    "usd_open": "from pxr import Usd; Usd.Stage.Open(os.path.join(PRIV, 'secret.usda'))",
    "usd_find_or_open": "from pxr import Sdf; Sdf.Layer.FindOrOpen(os.path.join(PRIV, 'secret.usda'))",
    "usd_create_startup": "from pxr import Usd; Usd.Stage.CreateNew(os.path.join(STARTUP, 'x.usda'))",
    "usd_export": "from pxr import Usd; Usd.Stage.CreateInMemory().Export(os.path.join(PRIV, 'out.usda'))",
    "usd_write_data_file": "from pxr import Sdf; Sdf.Layer.CreateAnonymous()._WriteDataFile(os.path.join(PRIV, 'o'))",
    "usd_zip_writer": "from pxr import Sdf; Sdf.ZipFileWriter.CreateNew(os.path.join(PRIV, 'out.usdz'))",
    "usd_zip_add_file": ("from pxr import Sdf\nw = Sdf.ZipFileWriter.CreateNew(os.path.join(PLAIN, 'add.usdz'))\n"
                         "w.AddFile(KEY)"),
    "usd_plug_register": "from pxr import Plug; Plug.Registry().RegisterPlugins(PLAIN)",
    "usd_touch": "from pxr import Tf; Tf.TouchFile(os.path.join(PRIV, 'touched'), True)",
    "oiio_input_open": "import OpenImageIO as o; o.ImageInput.open(os.path.join(PRIV, 'secret.exr'))",
    "oiio_imagebuf": "import OpenImageIO as o; o.ImageBuf(os.path.join(PRIV, 'secret.exr'))",
    "oiio_write": ("import OpenImageIO as o\n"
                   "o.ImageBuf(o.ImageSpec(2, 2, 3, 'float')).write(os.path.join(PRIV, 'out.exr'))"),
    "oiio_output_create": "import OpenImageIO as o; o.ImageOutput.create(os.path.join(PRIV, 'out.png'))",
    "oiio_cache": "import OpenImageIO as o; o.ImageCache(False).get_imagespec(os.path.join(PRIV, 'secret.exr'))",
    "oiio_cache_searchpath": "import OpenImageIO as o; o.ImageCache(False).attribute('searchpath', PRIV)",
    "oiio_cache_escape": ("import OpenImageIO as o\nc = o.ImageCache(False)\nc.attribute('searchpath', PLAIN)\n"
                          "c.get_imagespec(os.path.relpath(os.path.join(PRIV, 'secret.exr'), PLAIN))"),
    "oiio_texture": "import OpenImageIO as o; o.TextureSystem(False).imagespec(os.path.join(PRIV, 'secret.exr'))",
    "oiio_plugin_path": "import OpenImageIO as o; o.attribute('plugin_searchpath', PLAIN)",
    "oiio_plugin_argument": "import OpenImageIO as o; o.ImageInput.create('x.zzz', PLAIN)",
    "oiio_make_texture_read": ("import OpenImageIO as o; o.ImageBufAlgo.make_texture(o.MakeTxTexture, "
                               "os.path.join(PRIV, 'secret.exr'), os.path.join(PLAIN, 'out.tx'))"),
    "oiio_make_texture_write": ("import OpenImageIO as o; o.ImageBufAlgo.make_texture(o.MakeTxTexture, "
                                "os.path.join(PLAIN, 'oiio_a.exr'), os.path.join(PRIV, 'out.tx'))"),
    "oiio_color_config": "import OpenImageIO as o; o.ColorConfig(os.path.join(PRIV, 'secret.ocio'))",
    "oiio_lut": ("import OpenImageIO as o\nb = o.ImageBuf(o.ImageSpec(2, 2, 3, 'float'))\n"
                 "o.ImageBufAlgo.ociofiletransform(b, b, os.path.join(PRIV, 'secret.cube'))"),
    "oiio_font": ("import OpenImageIO as o\nb = o.ImageBuf(o.ImageSpec(8, 8, 3, 'float'))\n"
                  "o.ImageBufAlgo.render_text(b, 0, 0, 'x', 16, KEY)"),
    "exr_read": "import OpenEXR; OpenEXR.File(os.path.join(PRIV, 'secret_exr.exr'))",
    "exr_write": ("import OpenEXR, numpy\n"
                  "OpenEXR.File({}, {'R': numpy.zeros((2, 2), 'float32')}).write(os.path.join(PRIV, 'o.exr'))"),
    "exr_input_file": "import OpenEXR; OpenEXR.InputFile(os.path.join(PRIV, 'secret_exr.exr'))",
    "exr_probe": "import OpenEXR; OpenEXR.isOpenExrFile(os.path.join(PRIV, 'secret_exr.exr'))",
    "vdb_read": "import openvdb; openvdb.read(os.path.join(PRIV, 'secret.vdb'), 'plainmark')",
    "vdb_read_all": "import openvdb; openvdb.readAll(os.path.join(PRIV, 'secret.vdb'))",
    "vdb_metadata": "import openvdb; openvdb.readMetadata(os.path.join(PRIV, 'secret.vdb'))",
    "vdb_write": "import openvdb; openvdb.write(os.path.join(PRIV, 'out.vdb'), [openvdb.FloatGrid()])",
    "mx_read": "import MaterialX as mx; mx.readFromXmlFile(mx.createDocument(), os.path.join(PRIV, 'secret.mtlx'))",
    "mx_write": "import MaterialX as mx; mx.writeToXmlFile(mx.createDocument(), os.path.join(PRIV, 'out.mtlx'))",
    "mx_read_file": "import MaterialX as mx; mx.readFile(KEY)",
    "mx_search_path": ("import MaterialX as mx\n"
                       "mx.readFromXmlFile(mx.createDocument(), 'secret.mtlx', mx.FileSearchPath(PRIV))"),
    "mx_libraries": ("import MaterialX as mx\nmx.loadLibraries([os.path.basename(PRIV)], "
                     "mx.FileSearchPath(os.path.dirname(PRIV)), mx.createDocument())"),
    "mx_create_directory": "import MaterialX as mx; mx.FilePath(os.path.join(PRIV, 'made')).createDirectory()",
    "mx_list_directory": "import MaterialX as mx; mx.FilePath(PRIV).getFilesInDirectory('mtlx')",
    "mx_xinclude_reader": ("import MaterialX as mx\nmx.XmlReadOptions().readXIncludeFunction(mx.createDocument(), "
                           "mx.FilePath(os.path.join(PRIV, 'secret.mtlx')), mx.FileSearchPath(), None)"),
    "ocio_config_file": "import PyOpenColorIO as c; c.Config.CreateFromFile(os.path.join(PRIV, 'secret.ocio'))",
    "ocio_serialize": "import PyOpenColorIO as c; c.Config.CreateRaw().serialize(os.path.join(PRIV, 'out.ocio'))",
    "ocio_lut_key": ("import PyOpenColorIO as c\n"
                     "c.Config.CreateRaw().getProcessor(c.FileTransform(src=KEY + '.cube'))"),
    "ocio_lut": ("import PyOpenColorIO as c\n"
                 "c.Config.CreateRaw().getProcessor(c.FileTransform(src=os.path.join(PRIV, 'secret.cube')))"),
    "ocio_lut_search": ("import PyOpenColorIO as c\ncfg = c.Config.CreateRaw()\ncfg.setSearchPath(PRIV)\n"
                        "cfg.getProcessor(c.FileTransform(src='secret.cube'))"),
    "ocio_current": ("import PyOpenColorIO as c\n"
                     "c.GetCurrentConfig().getProcessor(c.FileTransform(src=os.path.join(PRIV, 'secret.cube')))"),
    "ocio_extract": "import PyOpenColorIO as c; c.ExtractOCIOZArchive(os.path.join(PLAIN, 'x.ocioz'), PRIV)",
    "otio_read": "import opentimelineio as t; t.adapters.read_from_file(os.path.join(PRIV, 'secret.otio'))",
    "otio_core_read": "import opentimelineio as t; t.core.deserialize_json_from_file(os.path.join(PRIV, 'secret.otio'))",
    "otio_from_json": "import opentimelineio as t; t.schema.Timeline.from_json_file(os.path.join(PRIV, 'secret.otio'))",
    "otio_to_json": "import opentimelineio as t; t.schema.Timeline().to_json_file(os.path.join(PRIV, 'out.otio'))",
    "otio_write": ("import opentimelineio as t\n"
                   "t.adapters.write_to_file(t.schema.Timeline(), os.path.join(PRIV, 'out2.otio'))"),
    "osl_open": "import oslquery; oslquery.OSLQuery(os.path.join(PRIV, 'secret.oso'))",
    "osl_search": "import oslquery; oslquery.OSLQuery().open('secret', PRIV)",
    "osl_escape": ("import oslquery\n"
                   "oslquery.OSLQuery().open(os.path.relpath(os.path.join(PRIV, 'secret'), PLAIN), PLAIN)"),
    "aud_sound": "import aud; aud.Sound(os.path.join(PRIV, 'secret.wav'))",
    "aud_file": "import aud; aud.Sound.file(os.path.join(PRIV, 'secret.wav'))",
    "aud_write": ("import aud\naud.Sound.sine(440).limit(0, 0.01).write(os.path.join(PRIV, 'out.wav'), rate=44100, "
                  "channels=aud.CHANNELS_MONO, format=aud.FORMAT_S16, container=aud.CONTAINER_WAV, "
                  "codec=aud.CODEC_PCM)"),
    "aud_hrtf": "import aud; aud.HRTF().loadLeftHrtfSet('.wav', PRIV)",
    "manta_save": ("import manta\ns = manta.Solver(name='x', gridSize=manta.vec3(4, 4, 4), dim=3)\n"
                   "g = s.create(manta.RealGrid)\ntry:\n    g.save(os.path.join(PRIV, 'out.uni'))\n"
                   "finally:\n    del g, s"),
    "manta_load": ("import manta\ns = manta.Solver(name='x', gridSize=manta.vec3(8, 8, 8), dim=3)\n"
                   "g = s.create(manta.RealGrid)\ntry:\n    g.load(os.path.join(PRIV, 'secret.uni'))\n"
                   "finally:\n    del g, s"),
    "manta_info": "import manta; manta.printUniFileInfoString(os.path.join(PRIV, 'secret.uni'))",
    "importlib_vdb": "import importlib; importlib.import_module('openvdb').readAll(os.path.join(PRIV, 'secret.vdb'))",
}

# Each returns what it reached; the harness checks that no private content is in it.
SEALED = {
    name: """
from pxr import Usd
try:
    stage = Usd.Stage.Open(os.path.join(PLAIN, %r))
    stage.Load()
    result = sorted(str(prim.GetPath()) for prim in stage.Traverse())
except Exception as error:
    result = "error: %%s" %% error
""" % file for name, file in (("usd_sublayer", "outer_sub.usda"), ("usd_relative", "outer_rel.usda"),
                               ("usd_link", "outer_link.usda"), ("usd_reference", "outer_ref.usda"),
                               ("usd_payload", "outer_payload.usda"), ("usd_mtlx", "outer_mtlx.usda"),
                               ("usd_mtlx_include", "outer_mtlx_include.usda"))
}
SEALED.update({
    "usd_author_reference": """
from pxr import Usd
stage = Usd.Stage.CreateInMemory()
stage.DefinePrim("/Ref").GetReferences().AddReference(os.path.join(PRIV, "secret.usda"), "/SECRETMARK_PRIM")
result = sorted(str(prim.GetPath()) for prim in stage.Traverse())
""",
    "usd_author_sublayer": """
from pxr import Usd
stage = Usd.Stage.CreateInMemory()
stage.GetRootLayer().subLayerPaths.append(os.path.join(PRIV, "secret.usda"))
result = sorted(str(prim.GetPath()) for prim in stage.Traverse())
""",
    "usd_resolved_asset": """
from pxr import Ar
asset = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(KEY))
result = None if asset is None else bytes(asset.GetBuffer()).decode("latin-1")
""",
    "usd_package_dependency": """
from pxr import Sdf, UsdUtils
result = UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(os.path.join(PLAIN, "tex_stage.usda")),
                                       os.path.join(PLAIN, "tex_stage.usdz"))
""",
    "usd_import_operator": """
bpy.ops.wm.usd_import(filepath=os.path.join(PLAIN, "outer_sub.usda"))
result = sorted(bpy.data.objects.keys())
""",
    "mx_string_include": """
import MaterialX as mx
doc = mx.createDocument()
try:
    mx.readFromXmlString(doc, '<materialx version="1.39"><xi:include href="%s"/></materialx>'
                         % os.path.join(PRIV, "secret.mtlx"))
except Exception as error:
    pass
result = [node.getName() for node in doc.getNodeDefs()]
""",
    "mx_file_include": """
import MaterialX as mx
doc = mx.createDocument()
try:
    mx.readFromXmlFile(doc, os.path.join(PLAIN, "inc_private.mtlx"))
except Exception:
    pass
result = [node.getName() for node in doc.getNodeDefs()]
""",
    "ocio_key_as_lut": """
import PyOpenColorIO as c
cfg = c.Config.CreateRaw()
cfg.setSearchPath(os.path.dirname(KEY))
try:
    cfg.getProcessor(c.FileTransform(src=os.path.basename(KEY)))
    result = "made"
except Exception as error:
    result = str(error)
""",
})
