# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every file-like entry point of the bundled pipeline libraries, classified (native_inventory.PIPELINE_*;
test_blender_confinement_pipeline lists them in the app). Gated ones map to a statement that calls them with a
private path (refused, bpy_angelo_guard_confine_pipeline_table.cc); the others say why they open no file by a path
of their own."""

# flake8: noqa: E501  One statement per entry.

PIPELINE_GATED = {
    'MaterialX.PyMaterialXFormat.FilePath.createDirectory':
        "import MaterialX as mx; mx.FilePath(os.path.join(PRIV, 'made')).createDirectory()",
    'MaterialX.PyMaterialXFormat.FilePath.getFilesInDirectory':
        "import MaterialX as mx; mx.FilePath(PRIV).getFilesInDirectory('mtlx')",
    'MaterialX.PyMaterialXFormat.FilePath.getSubDirectories':
        'import MaterialX as mx; mx.FilePath(PRIV).getSubDirectories()',
    'MaterialX.PyMaterialXFormat.loadDocuments':
        'import MaterialX as mx; mx.loadDocuments(mx.FilePath(PRIV), mx.FileSearchPath(), set(), set(), [], [])',
    'MaterialX.PyMaterialXFormat.loadLibraries':
        'import MaterialX as mx; mx.loadLibraries([os.path.basename(PRIV)], mx.FileSearchPath(os.path.dirname(PRIV)), mx.createDocument())',
    'MaterialX.PyMaterialXFormat.loadLibrary':
        "import MaterialX as mx; mx.loadLibrary(mx.FilePath(os.path.join(PRIV, 'secret.mtlx')), mx.createDocument())",
    'MaterialX.PyMaterialXFormat.readFile':
        'import MaterialX as mx; mx.readFile(mx.FilePath(KEY))',
    'MaterialX.PyMaterialXFormat.readFromXmlFileBase':
        "import MaterialX as mx; mx.readFromXmlFileBase(mx.createDocument(), os.path.join(PRIV, 'secret.mtlx'))",
    'MaterialX.PyMaterialXFormat.readFromXmlString':
        'import MaterialX as mx; mx.readFromXmlString(mx.createDocument(), \'<materialx version="1.39"><xi:include href="%s"/></materialx>\' % os.path.join(PRIV, \'secret.mtlx\'))',
    'MaterialX.PyMaterialXFormat.writeToXmlFile':
        "import MaterialX as mx; mx.writeToXmlFile(mx.createDocument(), os.path.join(PRIV, 'o.mtlx'))",
    'MaterialX.PyMaterialXGenShader.GenContext.registerSourceCodeSearchPath':
        'from MaterialX import PyMaterialXGenShader as g; g.GenContext.registerSourceCodeSearchPath(None, PRIV)',
    'MaterialX.PyMaterialXRender.CgltfLoader.load':
        'import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.CgltfLoader.load(None, mx.FilePath(KEY), [], False)',
    'MaterialX.PyMaterialXRender.GeometryHandler.loadGeometry':
        'import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.GeometryHandler.loadGeometry(None, mx.FilePath(KEY), False)',
    'MaterialX.PyMaterialXRender.GeometryLoader.load':
        'import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.GeometryLoader.load(None, mx.FilePath(KEY), [], False)',
    'MaterialX.PyMaterialXRender.ImageHandler.acquireImage':
        'import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.ImageHandler.acquireImage(None, mx.FilePath(KEY))',
    'MaterialX.PyMaterialXRender.ImageHandler.saveImage':
        "import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.ImageHandler.saveImage(None, mx.FilePath(os.path.join(PRIV, 'o.png')), None)",
    'MaterialX.PyMaterialXRender.ImageHandler.setSearchPath':
        'import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.ImageHandler.setSearchPath(None, mx.FileSearchPath(PRIV))',
    'MaterialX.PyMaterialXRender.ImageLoader.loadImage':
        'import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.ImageLoader.loadImage(None, mx.FilePath(KEY))',
    'MaterialX.PyMaterialXRender.ImageLoader.saveImage':
        "import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.ImageLoader.saveImage(None, mx.FilePath(os.path.join(PRIV, 'o.png')), None, False)",
    'MaterialX.PyMaterialXRender.StbImageLoader.loadImage':
        'import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.StbImageLoader.loadImage(None, mx.FilePath(KEY))',
    'MaterialX.PyMaterialXRender.StbImageLoader.saveImage':
        "import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.StbImageLoader.saveImage(None, mx.FilePath(os.path.join(PRIV, 'o.png')), None, False)",
    'MaterialX.PyMaterialXRender.TinyObjLoader.load':
        'import MaterialX as mx; from MaterialX import PyMaterialXRender as r; r.TinyObjLoader.load(None, mx.FilePath(KEY), [], False)',
    'OpenEXR.File.__init__':
        "import OpenEXR; OpenEXR.File(os.path.join(PRIV, 'secret_exr.exr'))",
    'OpenEXR.File.write':
        "import OpenEXR, numpy; OpenEXR.File({}, {'R': numpy.zeros((2, 2), 'float32')}).write(os.path.join(PRIV, 'o.exr'))",
    'OpenEXR.InputFile.__init__':
        "import OpenEXR; OpenEXR.InputFile(os.path.join(PRIV, 'secret_exr.exr'))",
    'OpenEXR.InputFile.__new__':
        "import OpenEXR; OpenEXR.InputFile.__new__(OpenEXR.InputFile, os.path.join(PRIV, 'secret_exr.exr'))",
    'OpenEXR.OutputFile.__init__':
        "import OpenEXR; OpenEXR.OutputFile(os.path.join(PRIV, 'o2.exr'), OpenEXR.Header(2, 2))",
    'OpenEXR.OutputFile.__new__':
        "import OpenEXR; OpenEXR.OutputFile.__new__(OpenEXR.OutputFile, os.path.join(PRIV, 'o3.exr'), OpenEXR.Header(2, 2))",
    'OpenEXR.isOpenExrFile':
        "import OpenEXR; OpenEXR.isOpenExrFile(os.path.join(PRIV, 'secret_exr.exr'))",
    'OpenImageIO.OpenImageIO.ColorConfig.__init__':
        "import OpenImageIO as o; o.ColorConfig(os.path.join(PRIV, 'secret.ocio'))",
    'OpenImageIO.OpenImageIO.ColorConfig.default_colorconfig':
        "import OpenImageIO as o\nos.environ['OCIO'] = os.path.join(PRIV, 'secret.ocio')\ntry:\n    o.ColorConfig.default_colorconfig()\nfinally:\n    del os.environ['OCIO']",
    'OpenImageIO.OpenImageIO.ImageBuf.__init__':
        "import OpenImageIO as o; o.ImageBuf(os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.ImageBuf.init_spec':
        "import OpenImageIO as o; o.ImageBuf().init_spec(os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.ImageBuf.reset':
        "import OpenImageIO as o; o.ImageBuf().reset(os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.ImageBuf.write':
        "import OpenImageIO as o; o.ImageBuf(o.ImageSpec(2, 2, 3, 'float')).write(os.path.join(PRIV, 'o.exr'))",
    'OpenImageIO.OpenImageIO.ImageBufAlgo.colorconvert':
        "import OpenImageIO as o\nos.environ['OCIO'] = os.path.join(PRIV, 'secret.ocio')\ntry:\n    b = o.ImageBuf(o.ImageSpec(2, 2, 3, 'float')); o.ImageBufAlgo.colorconvert(b, b, 'raw', 'raw')\nfinally:\n    del os.environ['OCIO']",
    'OpenImageIO.OpenImageIO.ImageBufAlgo.make_texture':
        "import OpenImageIO as o; o.ImageBufAlgo.make_texture(o.MakeTxTexture, os.path.join(PRIV, 'secret.exr'), os.path.join(PLAIN, 'o.tx'))",
    'OpenImageIO.OpenImageIO.ImageBufAlgo.ociodisplay':
        "import OpenImageIO as o\nos.environ['OCIO'] = os.path.join(PRIV, 'secret.ocio')\ntry:\n    b = o.ImageBuf(o.ImageSpec(2, 2, 3, 'float')); o.ImageBufAlgo.ociodisplay(b, b, '', '')\nfinally:\n    del os.environ['OCIO']",
    'OpenImageIO.OpenImageIO.ImageBufAlgo.ociofiletransform':
        "import OpenImageIO as o; b = o.ImageBuf(o.ImageSpec(2, 2, 3, 'float')); o.ImageBufAlgo.ociofiletransform(b, b, os.path.join(PRIV, 'secret.cube'))",
    'OpenImageIO.OpenImageIO.ImageBufAlgo.ociolook':
        "import OpenImageIO as o\nos.environ['OCIO'] = os.path.join(PRIV, 'secret.ocio')\ntry:\n    b = o.ImageBuf(o.ImageSpec(2, 2, 3, 'float')); o.ImageBufAlgo.ociolook(b, b, 'x')\nfinally:\n    del os.environ['OCIO']",
    'OpenImageIO.OpenImageIO.ImageBufAlgo.ocionamedtransform':
        "import OpenImageIO as o\nos.environ['OCIO'] = os.path.join(PRIV, 'secret.ocio')\ntry:\n    b = o.ImageBuf(o.ImageSpec(2, 2, 3, 'float')); o.ImageBufAlgo.ocionamedtransform(b, b, 'x')\nfinally:\n    del os.environ['OCIO']",
    'OpenImageIO.OpenImageIO.ImageBufAlgo.render_text':
        "import OpenImageIO as o; o.ImageBufAlgo.render_text(o.ImageBuf(o.ImageSpec(8, 8, 3, 'float')), 0, 0, 'x', 16, KEY)",
    'OpenImageIO.OpenImageIO.ImageBufAlgo.text_size':
        "import OpenImageIO as o; o.ImageBufAlgo.text_size('x', 16, KEY)",
    'OpenImageIO.OpenImageIO.ImageCache.attribute':
        "import OpenImageIO as o; o.ImageCache(False).attribute('searchpath', PRIV)",
    'OpenImageIO.OpenImageIO.ImageCache.get_cache_dimensions':
        "import OpenImageIO as o; o.ImageCache(False).get_cache_dimensions(os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.ImageCache.get_imagespec':
        "import OpenImageIO as o; o.ImageCache(False).get_imagespec(os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.ImageCache.get_pixels':
        "import OpenImageIO as o; o.ImageCache(False).get_pixels(os.path.join(PRIV, 'secret.exr'), 0, 0, 0, 1, 0, 1, 0, 1)",
    'OpenImageIO.OpenImageIO.ImageInput.create':
        "import OpenImageIO as o; o.ImageInput.create(os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.ImageInput.open':
        "import OpenImageIO as o; o.ImageInput.open(os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.ImageInput.valid_file':
        "import OpenImageIO as o; o.ImageInput.valid_file(None, os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.ImageOutput.create':
        "import OpenImageIO as o; o.ImageOutput.create(os.path.join(PRIV, 'o.png'))",
    'OpenImageIO.OpenImageIO.ImageOutput.open':
        "import OpenImageIO as o; o.ImageOutput.create('x.png').open(os.path.join(PRIV, 'o.png'), o.ImageSpec(2, 2, 3, 'uint8'))",
    'OpenImageIO.OpenImageIO.TextureSystem.attribute':
        "import OpenImageIO as o; o.TextureSystem(False).attribute('searchpath', PRIV)",
    'OpenImageIO.OpenImageIO.TextureSystem.environment':
        "import OpenImageIO as o; o.TextureSystem(False).environment(os.path.join(PRIV, 'secret.exr'), o.TextureOpt(), (0, 0, 1), (0, 0, 0), (0, 0, 0))",
    'OpenImageIO.OpenImageIO.TextureSystem.imagespec':
        "import OpenImageIO as o; o.TextureSystem(False).imagespec(os.path.join(PRIV, 'secret.exr'))",
    'OpenImageIO.OpenImageIO.TextureSystem.inventory_udim':
        "import OpenImageIO as o; o.TextureSystem(False).inventory_udim(os.path.join(PRIV, 'tex.<UDIM>.exr'))",
    'OpenImageIO.OpenImageIO.TextureSystem.texture':
        "import OpenImageIO as o; o.TextureSystem(False).texture(os.path.join(PRIV, 'secret.exr'), o.TextureOpt(), 0.5, 0.5, 0, 0, 0, 0)",
    'OpenImageIO.OpenImageIO.TextureSystem.texture3d':
        "import OpenImageIO as o; o.TextureSystem(False).texture3d(os.path.join(PRIV, 'secret.exr'), o.TextureOpt(), (0, 0, 0), (0, 0, 0), (0, 0, 0), (0, 0, 0))",
    'OpenImageIO.OpenImageIO.attribute':
        "import OpenImageIO as o; o.attribute('plugin_searchpath', PLAIN)",
    'PyOpenColorIO.ExtractOCIOZArchive':
        "import PyOpenColorIO as c; c.ExtractOCIOZArchive(os.path.join(PLAIN, 'x.ocioz'), PRIV)",
    'PyOpenColorIO.GetCurrentConfig':
        "import PyOpenColorIO as c\nos.environ['OCIO'] = os.path.join(PRIV, 'secret.ocio')\ntry:\n    c.GetCurrentConfig()\nfinally:\n    del os.environ['OCIO']",
    'PyOpenColorIO.PyOpenColorIO.Baker.bake':
        "import PyOpenColorIO as c; b = c.Baker(); b.setConfig(c.Config.CreateRaw()); b.bake(os.path.join(PRIV, 'o.cube'))",
    'PyOpenColorIO.PyOpenColorIO.CDLTransform.CreateFromFile':
        "import PyOpenColorIO as c; c.CDLTransform.CreateFromFile(KEY, '')",
    'PyOpenColorIO.PyOpenColorIO.CDLTransform.CreateGroupFromFile':
        'import PyOpenColorIO as c; c.CDLTransform.CreateGroupFromFile(KEY)',
    'PyOpenColorIO.PyOpenColorIO.Config.CreateFromEnv':
        "import PyOpenColorIO as c\nos.environ['OCIO'] = os.path.join(PRIV, 'secret.ocio')\ntry:\n    c.Config.CreateFromEnv()\nfinally:\n    del os.environ['OCIO']",
    'PyOpenColorIO.PyOpenColorIO.Config.CreateFromFile':
        "import PyOpenColorIO as c; c.Config.CreateFromFile(os.path.join(PRIV, 'secret.ocio'))",
    'PyOpenColorIO.PyOpenColorIO.Config.GetProcessorFromBuiltinColorSpace':
        "import PyOpenColorIO as c\ncfg = c.Config.CreateFromFile('ocio://default')\ncs = c.ColorSpace(name='lut')\ncs.setTransform(c.FileTransform(src=os.path.join(PRIV, 'secret.cube')), c.COLORSPACE_DIR_TO_REFERENCE)\ncfg.addColorSpace(cs)\nc.Config.GetProcessorFromBuiltinColorSpace('ACEScg', cfg, 'lut')",
    'PyOpenColorIO.PyOpenColorIO.Config.GetProcessorFromConfigs':
        "import PyOpenColorIO as c\ncfg = c.Config.CreateFromFile('ocio://default')\ncs = c.ColorSpace(name='lut')\ncs.setTransform(c.FileTransform(src=os.path.join(PRIV, 'secret.cube')), c.COLORSPACE_DIR_TO_REFERENCE)\ncfg.addColorSpace(cs)\nc.Config.GetProcessorFromConfigs(cfg, 'lut', cfg, 'ACEScg')",
    'PyOpenColorIO.PyOpenColorIO.Config.GetProcessorToBuiltinColorSpace':
        "import PyOpenColorIO as c\ncfg = c.Config.CreateFromFile('ocio://default')\ncs = c.ColorSpace(name='lut')\ncs.setTransform(c.FileTransform(src=os.path.join(PRIV, 'secret.cube')), c.COLORSPACE_DIR_TO_REFERENCE)\ncfg.addColorSpace(cs)\nc.Config.GetProcessorToBuiltinColorSpace(cfg, 'lut', 'ACEScg')",
    'PyOpenColorIO.PyOpenColorIO.Config.archive':
        "import PyOpenColorIO as c; c.Config.CreateRaw().archive(os.path.join(PRIV, 'o.ocioz'))",
    'PyOpenColorIO.PyOpenColorIO.Config.getProcessor':
        "import PyOpenColorIO as c\ncfg = c.Config.CreateFromFile('ocio://default')\ncs = c.ColorSpace(name='lut')\ncs.setTransform(c.FileTransform(src=os.path.join(PRIV, 'secret.cube')), c.COLORSPACE_DIR_TO_REFERENCE)\ncfg.addColorSpace(cs)\ncfg.getProcessor('lut', 'ACEScg')",
    'PyOpenColorIO.PyOpenColorIO.Config.instantiateDisplayFromICCProfile':
        'import PyOpenColorIO as c; c.Config.CreateRaw().instantiateDisplayFromICCProfile(KEY)',
    'PyOpenColorIO.PyOpenColorIO.Config.serialize':
        "import PyOpenColorIO as c; c.Config.CreateRaw().serialize(os.path.join(PRIV, 'o.ocio'))",
    'PyOpenColorIO.PyOpenColorIO.ConfigMerger.CreateFromFile':
        "import PyOpenColorIO as c; c.ConfigMerger.CreateFromFile(os.path.join(PRIV, 'secret.ocio'))",
    'PyOpenColorIO.PyOpenColorIO.GroupTransform.write':
        "import PyOpenColorIO as c; c.GroupTransform().write('Academy/ASC Common LUT Format', os.path.join(PRIV, 'o.clf'), c.Config.CreateRaw())",
    'PyOpenColorIO.PyOpenColorIO.LegacyViewingPipeline.getProcessor':
        "import PyOpenColorIO as c\ncfg = c.Config.CreateFromFile('ocio://default')\ncs = c.ColorSpace(name='lut')\ncs.setTransform(c.FileTransform(src=os.path.join(PRIV, 'secret.cube')), c.COLORSPACE_DIR_TO_REFERENCE)\ncfg.addColorSpace(cs)\nd = cfg.getDefaultDisplay()\nlp = c.LegacyViewingPipeline()\nlp.setLinearCC(c.ColorSpaceTransform(src='ACEScg', dst='lut'))\nlp.setDisplayViewTransform(c.DisplayViewTransform(src='ACEScg', display=d, view=cfg.getDefaultView(d)))\nlp.getProcessor(cfg)",
    'PyOpenColorIO.SetCurrentConfig':
        "import PyOpenColorIO as c\ncfg = c.Config.CreateFromFile('ocio://default')\ncs = c.ColorSpace(name='lut')\ncs.setTransform(c.FileTransform(src=os.path.join(PRIV, 'secret.cube')), c.COLORSPACE_DIR_TO_REFERENCE)\ncfg.addColorSpace(cs)\nc.SetCurrentConfig(cfg)\nc.GetCurrentConfig().getProcessor('lut', 'ACEScg')",
    'aud.HRTF.loadLeftHrtfSet':
        "import aud; aud.HRTF().loadLeftHrtfSet('.wav', PRIV)",
    'aud.HRTF.loadRightHrtfSet':
        "import aud; aud.HRTF().loadRightHrtfSet('.wav', PRIV)",
    'aud.Sound.__new__':
        "import aud; aud.Sound(os.path.join(PRIV, 'secret.wav'))",
    'aud.Sound.file':
        "import aud; aud.Sound.file(os.path.join(PRIV, 'secret.wav'))",
    'aud.Sound.write':
        "import aud; aud.Sound.sine(440).limit(0, 0.01).write(os.path.join(PRIV, 'o.wav'), rate=44100, channels=aud.CHANNELS_MONO, format=aud.FORMAT_S16, container=aud.CONTAINER_WAV, codec=aud.CODEC_PCM)",
    'manta.BasicParticleSystem.load':
        "import manta; manta.BasicParticleSystem.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.BasicParticleSystem.readParticles':
        "import manta; manta.BasicParticleSystem.readParticles(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.BasicParticleSystem.save':
        "import manta; manta.BasicParticleSystem.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Grid4d_Real.load':
        "import manta; manta.Grid4d_Real.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.Grid4d_Real.save':
        "import manta; manta.Grid4d_Real.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Grid4d_Vec3.load':
        "import manta; manta.Grid4d_Vec3.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.Grid4d_Vec3.save':
        "import manta; manta.Grid4d_Vec3.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Grid4d_Vec4.load':
        "import manta; manta.Grid4d_Vec4.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.Grid4d_Vec4.save':
        "import manta; manta.Grid4d_Vec4.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Grid4d_int.load':
        "import manta; manta.Grid4d_int.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.Grid4d_int.save':
        "import manta; manta.Grid4d_int.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Grid_Real.load':
        "import manta; manta.Grid_Real.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.Grid_Real.save':
        "import manta; manta.Grid_Real.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Grid_Vec3.load':
        "import manta; manta.Grid_Vec3.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.Grid_Vec3.save':
        "import manta; manta.Grid_Vec3.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Grid_int.load':
        "import manta; manta.Grid_int.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.Grid_int.save':
        "import manta; manta.Grid_int.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Mesh.load':
        "import manta; manta.Mesh.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.Mesh.save':
        "import manta; manta.Mesh.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.MeshDataImpl_Real.load':
        "import manta; manta.MeshDataImpl_Real.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.MeshDataImpl_Real.save':
        "import manta; manta.MeshDataImpl_Real.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.MeshDataImpl_Vec3.load':
        "import manta; manta.MeshDataImpl_Vec3.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.MeshDataImpl_Vec3.save':
        "import manta; manta.MeshDataImpl_Vec3.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.MeshDataImpl_int.load':
        "import manta; manta.MeshDataImpl_int.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.MeshDataImpl_int.save':
        "import manta; manta.MeshDataImpl_int.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.ParticleDataImpl_Real.load':
        "import manta; manta.ParticleDataImpl_Real.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.ParticleDataImpl_Real.save':
        "import manta; manta.ParticleDataImpl_Real.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.ParticleDataImpl_Vec3.load':
        "import manta; manta.ParticleDataImpl_Vec3.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.ParticleDataImpl_Vec3.save':
        "import manta; manta.ParticleDataImpl_Vec3.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.ParticleDataImpl_int.load':
        "import manta; manta.ParticleDataImpl_int.load(None, os.path.join(PRIV, 'secret.uni'))",
    'manta.ParticleDataImpl_int.save':
        "import manta; manta.ParticleDataImpl_int.save(None, os.path.join(PRIV, 'o.uni'))",
    'manta.Timings.saveMean':
        "import manta; manta.Timings.saveMean(None, os.path.join(PRIV, 'o.uni'))",
    'manta.getNpzFileSize':
        "import manta; manta.getNpzFileSize(os.path.join(PRIV, 'secret.uni'))",
    'manta.getUniFileSize':
        "import manta; manta.getUniFileSize(os.path.join(PRIV, 'secret.uni'))",
    'manta.load':
        "import manta; manta.load(name=os.path.join(PRIV, 'secret.uni'), objects=[])",
    'manta.printUniFileInfoString':
        "import manta; manta.printUniFileInfoString(os.path.join(PRIV, 'secret.uni'))",
    'manta.save':
        "import manta; manta.save(name=os.path.join(PRIV, 'o.uni'), objects=[])",
    'opentimelineio._otio.SerializableObject.from_json_file':
        "import opentimelineio as t; t.core.SerializableObject.from_json_file(os.path.join(PRIV, 'secret.otio'))",
    'opentimelineio._otio.SerializableObject.to_json_file':
        "import opentimelineio as t; t.schema.Timeline().to_json_file(os.path.join(PRIV, 'o.otio'))",
    'opentimelineio._otio._serialize_json_to_file':
        "import opentimelineio as t; t._otio._serialize_json_to_file(t.schema.Timeline(), os.path.join(PRIV, 'o2.otio'), {}, 4)",
    'opentimelineio._otio.deserialize_json_from_file':
        "import opentimelineio as t; t._otio.deserialize_json_from_file(os.path.join(PRIV, 'secret.otio'))",
    'openvdb.read':
        "import openvdb; openvdb.read(os.path.join(PRIV, 'secret.vdb'), 'plainmark')",
    'openvdb.readAll':
        "import openvdb; openvdb.readAll(os.path.join(PRIV, 'secret.vdb'))",
    'openvdb.readAllGridMetadata':
        "import openvdb; openvdb.readAllGridMetadata(os.path.join(PRIV, 'secret.vdb'))",
    'openvdb.readGridMetadata':
        "import openvdb; openvdb.readGridMetadata(os.path.join(PRIV, 'secret.vdb'), 'plainmark')",
    'openvdb.readMetadata':
        "import openvdb; openvdb.readMetadata(os.path.join(PRIV, 'secret.vdb'))",
    'openvdb.write':
        "import openvdb; openvdb.write(os.path.join(PRIV, 'o.vdb'), [openvdb.FloatGrid()])",
    'oslquery.oslquery.OSLQuery.__init__':
        "import oslquery; oslquery.OSLQuery(os.path.join(PRIV, 'secret.oso'))",
    'oslquery.oslquery.OSLQuery.open':
        "import oslquery; oslquery.OSLQuery().open('secret', PRIV)",
    'pxr.Glf.DrawTarget.WriteToFile':
        "from pxr import Glf; Glf.DrawTarget.WriteToFile(None, 'color', os.path.join(PRIV, 'd.png'))",
    'pxr.Plug.Registry.RegisterPlugins':
        'from pxr import Plug; Plug.Registry().RegisterPlugins(PLAIN)',
    'pxr.Sdf.CrateInfo.Open':
        "from pxr import Sdf; Sdf.CrateInfo.Open(os.path.join(PRIV, 'x.usdc'))",
    'pxr.Sdf.Layer.CreateNew':
        "from pxr import Sdf; Sdf.Layer.CreateNew(os.path.join(PRIV, 'new2.usda'))",
    'pxr.Sdf.Layer.Export':
        "from pxr import Sdf; Sdf.Layer.CreateAnonymous().Export(os.path.join(PRIV, 'o2.usda'))",
    'pxr.Sdf.Layer.FindOrOpen':
        "from pxr import Sdf; Sdf.Layer.FindOrOpen(os.path.join(PRIV, 'secret.usda'))",
    'pxr.Sdf.Layer.Import':
        "from pxr import Sdf; Sdf.Layer.CreateAnonymous().Import(os.path.join(PRIV, 'secret.usda'))",
    'pxr.Sdf.Layer.OpenAsAnonymous':
        "from pxr import Sdf; Sdf.Layer.OpenAsAnonymous(os.path.join(PRIV, 'secret.usda'))",
    'pxr.Sdf.Layer._WriteDataFile':
        "from pxr import Sdf; Sdf.Layer.CreateAnonymous()._WriteDataFile(os.path.join(PRIV, 'o.txt'))",
    'pxr.Sdf.ZipFile.Open':
        "from pxr import Sdf; Sdf.ZipFile.Open(os.path.join(PRIV, 'x.usdz'))",
    'pxr.Sdf.ZipFileWriter.AddFile':
        "from pxr import Sdf; Sdf.ZipFileWriter.CreateNew(os.path.join(PLAIN, 'probe.usdz')).AddFile(KEY)",
    'pxr.Sdf.ZipFileWriter.CreateNew':
        "from pxr import Sdf; Sdf.ZipFileWriter.CreateNew(os.path.join(PRIV, 'o.usdz'))",
    'pxr.Sdr.FsHelpersDiscoverFiles':
        "from pxr import Sdr; Sdr.FsHelpersDiscoverFiles([PRIV], ['mtlx'], True)",
    'pxr.Tf.MallocTag.CallTree.LoadReport':
        'from pxr import Tf; Tf.MallocTag.CallTree.LoadReport(None, KEY)',
    'pxr.Tf.MallocTag.CallTree.Report':
        "from pxr import Tf; Tf.MallocTag.CallTree.Report(None, os.path.join(PRIV, 'm.txt'))",
    'pxr.Tf.ScriptModuleLoader.WriteDotFile':
        "from pxr import Tf; Tf.ScriptModuleLoader().WriteDotFile(os.path.join(PRIV, 'x.dot'))",
    'pxr.Tf.TouchFile':
        "from pxr import Tf; Tf.TouchFile(os.path.join(PRIV, 't'), True)",
    'pxr.Trace.Reporter.LoadReport':
        'from pxr import Trace; Trace.Reporter.LoadReport(KEY)',
    'pxr.Trace.Reporter.Report':
        "from pxr import Trace; Trace.Reporter.globalReporter.Report(os.path.join(PRIV, 'r.txt'))",
    'pxr.Trace.Reporter.ReportChromeTracingToFile':
        "from pxr import Trace; Trace.Reporter.globalReporter.ReportChromeTracingToFile(os.path.join(PRIV, 'r.json'))",
    'pxr.Usd.Stage.CreateNew':
        "from pxr import Usd; Usd.Stage.CreateNew(os.path.join(PRIV, 'new.usda'))",
    'pxr.Usd.Stage.Export':
        "from pxr import Usd; Usd.Stage.CreateInMemory().Export(os.path.join(PRIV, 'o.usda'))",
    'pxr.Usd.Stage.Open':
        "from pxr import Usd; Usd.Stage.Open(os.path.join(PRIV, 'secret.usda'))",
    'pxr.Usd.Stage.OpenMasked':
        "from pxr import Usd; Usd.Stage.OpenMasked(os.path.join(PRIV, 'secret.usda'), Usd.StagePopulationMask())",
    'pxr.UsdAppUtils.FrameRecorder.Record':
        "from pxr import UsdAppUtils; UsdAppUtils.FrameRecorder.Record(None, None, None, None, os.path.join(PRIV, 'f.png'))",
    'pxr.UsdMtlx._TestFile':
        "from pxr import UsdMtlx; UsdMtlx._TestFile(os.path.join(PRIV, 'secret.mtlx'))",
    'pxr.UsdProfiles.ProfileRegistry._TestLoadFromFile':
        'from pxr import UsdProfiles; UsdProfiles.ProfileRegistry._TestLoadFromFile(KEY)',
    'pxr.UsdUtils.CreateNewARKitUsdzPackage':
        "from pxr import UsdUtils, Sdf; UsdUtils.CreateNewARKitUsdzPackage(Sdf.AssetPath(os.path.join(PLAIN, 'usd_top.usda')), os.path.join(PRIV, 'o2.usdz'))",
    'pxr.UsdUtils.CreateNewUsdzPackage':
        "from pxr import UsdUtils, Sdf; UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(os.path.join(PLAIN, 'usd_top.usda')), os.path.join(PRIV, 'o.usdz'))",
}
_WHY = {
    'AR': 'its asset paths go through the confined USD resolver',
    'MEMORY': 'works on data in memory; opens no file by a path',
    'NAMES': 'names, attributes, settings or strings; no file is opened',
    'PROBE': 'resolves a path or tests that it exists; reads no file',
    'OPENED': 'reads or writes the file its object opened, judged when it was opened',
    'INSTALLED': 'loads only what the installed libraries registered',
    'PYTHON': "Python code: its file I/O goes through Python's own audited calls (or a gated native call)",
    'LATER': 'sets search paths or LUT names a processor reads later through the judging IO proxy',
    'PROGRAMS': 'used only by OslRenderer, which starts programs (refused to scripts)',
    'PROXY': 'the IO proxy interface itself',
    'COPY': 'makes processors from the configuration it was given, a judged copy (its constructor is gated)',
}
PIPELINE_OTHER = {name: _WHY[why] for name, why in (
    ('MaterialX.PyMaterialXCore.AttributeDef.getExportable', 'NAMES'),
    ('MaterialX.PyMaterialXCore.AttributeDef.setExportable', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Document.importLibrary', 'MEMORY'),
    ('MaterialX.PyMaterialXCore.Element.getActiveFilePrefix', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Element.getFilePrefix', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Element.hasFilePrefix', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Element.setFilePrefix', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Implementation.getFile', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Implementation.hasFile', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Implementation.setFile', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Input.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXCore.Output.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXCore.StringResolver.getFilePrefix', 'NAMES'),
    ('MaterialX.PyMaterialXCore.StringResolver.getFilenameSubstitutions', 'NAMES'),
    ('MaterialX.PyMaterialXCore.StringResolver.setFilePrefix', 'NAMES'),
    ('MaterialX.PyMaterialXCore.StringResolver.setFilenameSubstitution', 'NAMES'),
    ('MaterialX.PyMaterialXFormat.FilePath.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXFormat.FilePath.isAbsolute', 'PROBE'),
    ('MaterialX.PyMaterialXFormat.FilePath.isDirectory', 'PROBE'),
    ('MaterialX.PyMaterialXFormat.FileSearchPath.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXFormat.flattenFilenames', 'PROBE'),
    ('MaterialX.PyMaterialXFormat.getSourceSearchPath', 'NAMES'),
    ('MaterialX.PyMaterialXFormat.writeToXmlString', 'MEMORY'),
    ('MaterialX.PyMaterialXGenShader.ColorManagementSystem.loadLibrary', 'MEMORY'),
    ('MaterialX.PyMaterialXGenShader.GenContext.resolveSourceFile', 'PROBE'),
    ('MaterialX.PyMaterialXGenShader.UnitSystem.loadLibrary', 'MEMORY'),
    ('MaterialX.PyMaterialXRender.CgltfLoader.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXRender.GeometryHandler.addLoader', 'NAMES'),
    ('MaterialX.PyMaterialXRender.GeometryLoader.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXRender.Image.__init__', 'MEMORY'),
    ('MaterialX.PyMaterialXRender.ImageBufferDeallocator.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXRender.ImageHandler.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXRender.ImageHandler.addLoader', 'NAMES'),
    ('MaterialX.PyMaterialXRender.ImageHandler.getFilenameResolver', 'NAMES'),
    ('MaterialX.PyMaterialXRender.ImageHandler.getSearchPath', 'NAMES'),
    ('MaterialX.PyMaterialXRender.ImageHandler.setFilenameResolver', 'NAMES'),
    ('MaterialX.PyMaterialXRender.ImageLoader.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXRender.ImageSamplingProperties.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXRender.StbImageLoader.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXRender.TinyObjLoader.__init__', 'NAMES'),
    ('MaterialX.PyMaterialXRenderOsl.OslRenderer.setOslOutputFilePath', 'PROGRAMS'),
    ('MaterialX.PyMaterialXRenderOsl.OslRenderer.setOslTestRenderSceneTemplateFile', 'PROGRAMS'),
    ('MaterialX.PyMaterialXRenderOsl.OslRenderer.setOslUtilityOSOPath', 'PROGRAMS'),
    ('MaterialX.getDefaultDataLibraryFolders', 'PROBE'),
    ('MaterialX.getDefaultDataSearchPath', 'PROBE'),
    ('OpenEXR.OutputFile.writePixels', 'OPENED'),
    ('OpenEXR.PreviewImage.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ColorConfig.filepathOnlyMatchesDefaultRule', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ColorConfig.getColorSpaceFromFilepath', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ImageBuf.read', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageBuf.set_write_format', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ImageBuf.set_write_tiles', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ImageBufAlgo.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ImageCache.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ImageCache.resolve_filename', 'PROBE'),
    ('OpenImageIO.OpenImageIO.ImageInput.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ImageInput.read_image', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageInput.read_native_deep_image', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageInput.read_native_deep_scanlines', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageInput.read_native_deep_tiles', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageInput.read_scanline', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageInput.read_scanlines', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageInput.read_tile', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageInput.read_tiles', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageOutput.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ImageOutput.write_deep_image', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageOutput.write_deep_scanlines', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageOutput.write_deep_tiles', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageOutput.write_image', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageOutput.write_scanline', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageOutput.write_scanlines', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageOutput.write_tile', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageOutput.write_tiles', 'OPENED'),
    ('OpenImageIO.OpenImageIO.ImageSpec.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.ImageSpec.serialize', 'NAMES'),
    ('OpenImageIO.OpenImageIO.MakeTextureMode.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.TextureOpt.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.TextureSystem.__init__', 'NAMES'),
    ('OpenImageIO.OpenImageIO.TextureSystem.resolve_filename', 'PROBE'),
    ('PyOpenColorIO.PyOpenColorIO.BuiltinConfigRegistry.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.SearchPathIterator', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Config.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.addSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Config.clearProcessorCache', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.clearSearchPaths', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Config.filepathOnlyMatchesDefaultRule', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.getColorSpaceFromFilepath', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.getFileRules', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.getSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Config.getSearchPaths', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Config.loadEnvironment', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.setFileRules', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.setProcessorCacheFlags', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Config.setSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMerger.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMerger.addSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMerger.getNumSearchPaths', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMerger.getSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMerger.serialize', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMerger.setSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMergingParameters.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMergingParameters.addSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMergingParameters.getFileRules', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMergingParameters.getSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMergingParameters.setFileRules', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ConfigMergingParameters.setSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Context.SearchPathIterator', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Context.addSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Context.clearSearchPaths', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Context.getSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Context.getSearchPaths', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.Context.loadEnvironment', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Context.resolveFileLocation', 'PROBE'),
    ('PyOpenColorIO.PyOpenColorIO.Context.setSearchPath', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.FileRules.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.FileTransform.__init__', 'LATER'),
    ('PyOpenColorIO.PyOpenColorIO.GroupTransform.GetWriteFormats', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.GroupTransform.WriteFormatIterator', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ImageDesc.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Lut1DTransform.getFileOutputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Lut1DTransform.setFileOutputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Lut3DTransform.getFileOutputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Lut3DTransform.setFileOutputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.MatrixTransform.getFileInputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.MatrixTransform.getFileOutputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.MatrixTransform.setFileInputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.MatrixTransform.setFileOutputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.MixingColorSpaceManager.getProcessor', 'COPY'),
    ('PyOpenColorIO.PyOpenColorIO.PackedImageDesc.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.PlanarImageDesc.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.Processor.getDefaultCPUProcessor', 'MEMORY'),
    ('PyOpenColorIO.PyOpenColorIO.Processor.getDefaultGPUProcessor', 'MEMORY'),
    ('PyOpenColorIO.PyOpenColorIO.Processor.getOptimizedCPUProcessor', 'MEMORY'),
    ('PyOpenColorIO.PyOpenColorIO.Processor.getOptimizedGPUProcessor', 'MEMORY'),
    ('PyOpenColorIO.PyOpenColorIO.Processor.getOptimizedProcessor', 'MEMORY'),
    ('PyOpenColorIO.PyOpenColorIO.Processor.getProcessorMetadata', 'MEMORY'),
    ('PyOpenColorIO.PyOpenColorIO.ProcessorCacheFlags.__init__', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ProcessorMetadata.FileIterator', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ProcessorMetadata.addFile', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.ProcessorMetadata.getFiles', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.PyConfigIOProxy.__init__', 'PROXY'),
    ('PyOpenColorIO.PyOpenColorIO.PyConfigIOProxy.getFastLutFileHash', 'PROXY'),
    ('PyOpenColorIO.PyOpenColorIO.PyConfigIOProxy.getLutData', 'PROXY'),
    ('PyOpenColorIO.PyOpenColorIO.RangeTransform.getFileInputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.RangeTransform.getFileOutputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.RangeTransform.setFileInputBitDepth', 'NAMES'),
    ('PyOpenColorIO.PyOpenColorIO.RangeTransform.setFileOutputBitDepth', 'NAMES'),
    ('aud.AnimateableProperty.read', 'MEMORY'),
    ('aud.AnimateableProperty.readSingle', 'MEMORY'),
    ('aud.AnimateableProperty.write', 'MEMORY'),
    ('aud.AnimateableProperty.writeConstantRange', 'MEMORY'),
    ('manta.setOpenBound', 'NAMES'),
    ('opentimelineio._otio.ImageSequenceReference.__init__', 'NAMES'),
    ('opentimelineio._otio._serialize_json_to_string', 'MEMORY'),
    ('opentimelineio._otio.deserialize_json_from_string', 'MEMORY'),
    ('opentimelineio._otio.set_type_record', 'MEMORY'),
    ('opentimelineio.adapters.adapter.Adapter.plugin_info_map', 'PYTHON'),
    ('opentimelineio.adapters.adapter.Adapter.read_from_file', 'PYTHON'),
    ('opentimelineio.adapters.adapter.Adapter.read_from_string', 'PYTHON'),
    ('opentimelineio.adapters.adapter.Adapter.write_to_file', 'PYTHON'),
    ('opentimelineio.adapters.adapter.Adapter.write_to_string', 'PYTHON'),
    ('opentimelineio.adapters.from_filepath', 'PYTHON'),
    ('opentimelineio.adapters.otio_json.read_from_file', 'PYTHON'),
    ('opentimelineio.adapters.otio_json.read_from_string', 'PYTHON'),
    ('opentimelineio.adapters.otio_json.write_to_file', 'PYTHON'),
    ('opentimelineio.adapters.otio_json.write_to_string', 'PYTHON'),
    ('opentimelineio.adapters.otiod.read_from_file', 'PYTHON'),
    ('opentimelineio.adapters.otiod.write_to_file', 'PYTHON'),
    ('opentimelineio.adapters.otioz.read_from_file', 'PYTHON'),
    ('opentimelineio.adapters.otioz.write_to_file', 'PYTHON'),
    ('opentimelineio.adapters.read_from_file', 'PYTHON'),
    ('opentimelineio.adapters.read_from_string', 'PYTHON'),
    ('opentimelineio.adapters.write_to_file', 'PYTHON'),
    ('opentimelineio.adapters.write_to_string', 'PYTHON'),
    ('opentimelineio.core.serialize_json_to_file', 'PYTHON'),
    ('opentimelineio.core.serialize_json_to_string', 'PYTHON'),
    ('opentimelineio.media_linker.MediaLinker.plugin_info_map', 'PYTHON'),
    ('opentimelineio.plugins.manifest.Manifest._update_plugin_source', 'PYTHON'),
    ('opentimelineio.plugins.manifest.Manifest.from_filepath', 'PYTHON'),
    ('opentimelineio.plugins.manifest.load_manifest', 'PYTHON'),
    ('opentimelineio.plugins.manifest.manifest_from_file', 'PYTHON'),
    ('opentimelineio.plugins.manifest.plugin_entry_points', 'PYTHON'),
    ('opentimelineio.plugins.plugin_info_map', 'PYTHON'),
    ('opentimelineio.plugins.python_plugin.PythonPlugin._imported_module', 'PYTHON'),
    ('opentimelineio.plugins.python_plugin.PythonPlugin.plugin_info_map', 'PYTHON'),
    ('opentimelineio.schema.schemadef.SchemaDef.plugin_info_map', 'PYTHON'),
    ('opentimelineio.url_utils.filepath_from_url', 'PYTHON'),
    ('opentimelineio.url_utils.url_from_filepath', 'PYTHON'),
    ('oslquery.oslquery.OSLQuery.open_bytecode', 'MEMORY'),
    ('pxr.Ar.Ar_PyAsset.Read', 'AR'),
    ('pxr.Ar.DefaultResolver.SetDefaultSearchPath', 'PROBE'),
    ('pxr.Ar.DefaultResolverContext.GetSearchPath', 'PROBE'),
    ('pxr.Ar.IsPackageRelativePath', 'AR'),
    ('pxr.Ar.JoinPackageRelativePath', 'PROBE'),
    ('pxr.Ar.Resolver.CanWriteAssetToPath', 'AR'),
    ('pxr.Ar.Resolver.OpenAsset', 'AR'),
    ('pxr.Ar.ResolverScopedCache.__init__', 'AR'),
    ('pxr.Ar.SplitPackageRelativePathInner', 'PROBE'),
    ('pxr.Ar.SplitPackageRelativePathOuter', 'PROBE'),
    ('pxr.Gf.Interval.IsMaxOpen', 'AR'),
    ('pxr.Gf.Interval.IsMinOpen', 'AR'),
    ('pxr.Glf.GLQueryObject.__init__', 'NAMES'),
    ('pxr.Glf.Texture.__init__', 'NAMES'),
    ('pxr.Pcp.Cache.GetDynamicFileFormatArgumentDependencyData', 'NAMES'),
    ('pxr.Pcp.Cache.HasAnyDynamicFileFormatArgumentAttributeDependencies', 'NAMES'),
    ('pxr.Pcp.Cache.HasAnyDynamicFileFormatArgumentFieldDependencies', 'NAMES'),
    ('pxr.Pcp.Cache.IsPayloadIncluded', 'AR'),
    ('pxr.Pcp.Cache.IsPossibleDynamicFileFormatArgumentAttribute', 'NAMES'),
    ('pxr.Pcp.Cache.IsPossibleDynamicFileFormatArgumentField', 'NAMES'),
    ('pxr.Pcp.Cache.Reload', 'AR'),
    ('pxr.Pcp.Cache.RequestPayloads', 'AR'),
    ('pxr.Pcp.Cache.__init__', 'NAMES'),
    ('pxr.Pcp.DynamicFileFormatDependencyData.CanAttributeDefaultValueChangeAffectFileFormatArguments', 'MEMORY'),
    ('pxr.Pcp.DynamicFileFormatDependencyData.CanFieldChangeAffectFileFormatArguments', 'MEMORY'),
    ('pxr.Pcp.DynamicFileFormatDependencyData.__init__', 'NAMES'),
    ('pxr.Plug.Notice.DidRegisterPlugins', 'INSTALLED'),
    ('pxr.Plug.Plugin.FindPluginResource', 'INSTALLED'),
    ('pxr.Plug.Plugin.Load', 'INSTALLED'),
    ('pxr.Plug.Registry.GetAllPlugins', 'INSTALLED'),
    ('pxr.Plug.Registry.GetPluginForType', 'INSTALLED'),
    ('pxr.Plug.Registry.GetPluginWithName', 'INSTALLED'),
    ('pxr.Plug.Registry.GetStringFromPluginMetaData', 'INSTALLED'),
    ('pxr.Plug._TestPlugBase1.TestAcceptPluginSequence', 'INSTALLED'),
    ('pxr.Plug._TestPlugBase2.TestAcceptPluginSequence', 'INSTALLED'),
    ('pxr.Plug._TestPlugBase3.TestAcceptPluginSequence', 'INSTALLED'),
    ('pxr.Plug._TestPlugBase4.TestAcceptPluginSequence', 'INSTALLED'),
    ('pxr.Sdf.AssetPathArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Sdf.AssetPathArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Sdf.CrateInfo.GetFileVersion', 'NAMES'),
    ('pxr.Sdf.FileFormat.CanRead', 'AR'),
    ('pxr.Sdf.FileFormat.FindAllDerivedFileFormatExtensions', 'MEMORY'),
    ('pxr.Sdf.FileFormat.FindAllFileFormatExtensions', 'MEMORY'),
    ('pxr.Sdf.FileFormat.FormatSupportsReading', 'AR'),
    ('pxr.Sdf.FileFormat.GetFileExtension', 'NAMES'),
    ('pxr.Sdf.FileFormat.GetFileExtensions', 'NAMES'),
    ('pxr.Sdf.FileFormat.IsPackage', 'AR'),
    ('pxr.Sdf.FileFormat.SupportsReading', 'AR'),
    ('pxr.Sdf.FileFormat.__init__', 'NAMES'),
    ('pxr.Sdf.Layer.ClearColorConfiguration', 'NAMES'),
    ('pxr.Sdf.Layer.ComputeAbsolutePath', 'PROBE'),
    ('pxr.Sdf.Layer.ExportToString', 'NAMES'),
    ('pxr.Sdf.Layer.FindOrOpenRelativeToLayer', 'AR'),
    ('pxr.Sdf.Layer.GetFileFormat', 'NAMES'),
    ('pxr.Sdf.Layer.GetFileFormatArguments', 'NAMES'),
    ('pxr.Sdf.Layer.GetLoadedLayers', 'AR'),
    ('pxr.Sdf.Layer.HasColorConfiguration', 'NAMES'),
    ('pxr.Sdf.Layer.ImportFromString', 'AR'),
    ('pxr.Sdf.Layer.Reload', 'AR'),
    ('pxr.Sdf.Layer.ReloadLayers', 'AR'),
    ('pxr.Sdf.Layer.Save', 'AR'),
    ('pxr.Sdf.Layer.SetPermissionToSave', 'AR'),
    ('pxr.Sdf.Notice.LayerDidReloadContent', 'AR'),
    ('pxr.Sdf.Path.GetAbsoluteRootOrPrimPath', 'NAMES'),
    ('pxr.Sdf.Path.IsAbsolutePath', 'NAMES'),
    ('pxr.Sdf.Path.IsAbsoluteRootOrPrimPath', 'NAMES'),
    ('pxr.Sdf.Path.IsAbsoluteRootPath', 'NAMES'),
    ('pxr.Sdf.Path.MakeAbsolutePath', 'MEMORY'),
    ('pxr.Sdf.PathExpression.IsAbsolute', 'NAMES'),
    ('pxr.Sdf.PathExpression.MakeAbsolute', 'MEMORY'),
    ('pxr.Sdf.PathExpressionArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Sdf.PathExpressionArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Sdf.PrimSpec.ClearPayloadList', 'AR'),
    ('pxr.Sdf.UsdFileFormat.__init__', 'NAMES'),
    ('pxr.Sdf.ZipFile.FileInfo', 'AR'),
    ('pxr.Sdf.ZipFile.GetFile', 'AR'),
    ('pxr.Sdf.ZipFile.GetFileInfo', 'AR'),
    ('pxr.Sdf.ZipFile.GetFileNames', 'AR'),
    ('pxr.Sdf.ZipFile.__init__', 'AR'),
    ('pxr.Sdf.ZipFileWriter.Save', 'AR'),
    ('pxr.Sdf.ZipFileWriter.__init__', 'AR'),
    ('pxr.Sdr.Registry.SetExtraDiscoveryPlugins', 'INSTALLED'),
    ('pxr.Sdr.Registry.SetExtraParserPlugins', 'INSTALLED'),
    ('pxr.Sdr.ShaderNode.GetOpenPages', 'AR'),
    ('pxr.Sdr.ShaderNodeMetadata.GetOpenPages', 'AR'),
    ('pxr.Sdr.ShaderNodeMetadata.HasOpenPages', 'AR'),
    ('pxr.Sdr.ShaderNodeQuery.__init__', 'NAMES'),
    ('pxr.Sdr.ShaderNodeQueryResult.__init__', 'NAMES'),
    ('pxr.Sdr.ShaderNodeQueryUtils.__init__', 'NAMES'),
    ('pxr.Tf.Debug.SetOutputFile', 'NAMES'),
    ('pxr.Tf.NamedTemporaryFile.__init__', 'PYTHON'),
    ('pxr.Tf.RefPtrTracker.GetAllTracesReport', 'NAMES'),
    ('pxr.Tf.RefPtrTracker.GetAllWatchedCountsReport', 'NAMES'),
    ('pxr.Tf.RefPtrTracker.GetTracesReportForWatched', 'NAMES'),
    ('pxr.Tf.ReportActiveErrorMarks', 'NAMES'),
    ('pxr.Tf.ScriptModuleLoader._LoadModulesForLibrary', 'AR'),
    ('pxr.Tf.ScriptModuleLoader.__init__', 'AR'),
    ('pxr.Tf.ScriptModuleLoader.__new__', 'AR'),
    ('pxr.Tf.WindowsImportWrapper', 'PYTHON'),   # Windows only: os.add_dll_directory, the app's own folder alone.
    ('pxr.Trace.Reporter.ReportChromeTracing', 'NAMES'),
    ('pxr.Trace.Reporter.ReportTimes', 'NAMES'),
    ('pxr.Ts.Spline.BakeInnerLoops', 'MEMORY'),
    ('pxr.Ts.Spline.GetKnotsWithInnerLoopsBaked', 'NAMES'),
    ('pxr.Ts.Spline.GetKnotsWithLoopsBaked', 'NAMES'),
    ('pxr.Usd.AttributeQuery.__init__', 'NAMES'),
    ('pxr.Usd.ColorSpaceCache.__init__', 'NAMES'),
    ('pxr.Usd.ColorSpaceHashCache.__init__', 'NAMES'),
    ('pxr.Usd.ModelAPI.GetPayloadAssetDependencies', 'AR'),
    ('pxr.Usd.ModelAPI.SetPayloadAssetDependencies', 'AR'),
    ('pxr.Usd.Payloads.AddInternalPayload', 'AR'),
    ('pxr.Usd.Payloads.AddPayload', 'AR'),
    ('pxr.Usd.Payloads.ClearPayloads', 'AR'),
    ('pxr.Usd.Payloads.RemovePayload', 'AR'),
    ('pxr.Usd.Payloads.SetPayloads', 'AR'),
    ('pxr.Usd.Prim.ClearPayload', 'AR'),
    ('pxr.Usd.Prim.GetPayloads', 'AR'),
    ('pxr.Usd.Prim.HasAuthoredPayloads', 'AR'),
    ('pxr.Usd.Prim.HasPayload', 'AR'),
    ('pxr.Usd.Prim.IsLoaded', 'AR'),
    ('pxr.Usd.Prim.Load', 'AR'),
    ('pxr.Usd.Prim.SetPayload', 'AR'),
    ('pxr.Usd.Prim.Unload', 'AR'),
    ('pxr.Usd.PrimCompositionQuery.__init__', 'NAMES'),
    ('pxr.Usd.Stage.ExportToString', 'NAMES'),
    ('pxr.Usd.Stage.FindLoadable', 'MEMORY'),
    ('pxr.Usd.Stage.GetColorConfigFallbacks', 'NAMES'),
    ('pxr.Usd.Stage.GetColorConfiguration', 'NAMES'),
    ('pxr.Usd.Stage.GetLoadRules', 'NAMES'),
    ('pxr.Usd.Stage.GetLoadSet', 'NAMES'),
    ('pxr.Usd.Stage.InitialLoadSet', 'NAMES'),
    ('pxr.Usd.Stage.IsSupportedFile', 'PROBE'),
    ('pxr.Usd.Stage.Load', 'AR'),
    ('pxr.Usd.Stage.LoadAndUnload', 'AR'),
    ('pxr.Usd.Stage.Reload', 'AR'),
    ('pxr.Usd.Stage.Save', 'AR'),
    ('pxr.Usd.Stage.SaveSessionLayers', 'AR'),
    ('pxr.Usd.Stage.SetColorConfigFallbacks', 'NAMES'),
    ('pxr.Usd.Stage.SetColorConfiguration', 'NAMES'),
    ('pxr.Usd.Stage.SetLoadRules', 'NAMES'),
    ('pxr.Usd.Stage.Unload', 'AR'),
    ('pxr.Usd.Stage.WriteFallbackPrimTypes', 'NAMES'),
    ('pxr.Usd.StageCache.__init__', 'NAMES'),
    ('pxr.Usd.StageCacheContext.__init__', 'NAMES'),
    ('pxr.Usd.StageCacheContextBlockType.__init__', 'NAMES'),
    ('pxr.Usd.StageLoadRules.IsLoaded', 'NAMES'),
    ('pxr.Usd.StageLoadRules.IsLoadedWithAllDescendants', 'NAMES'),
    ('pxr.Usd.StageLoadRules.IsLoadedWithNoDescendants', 'NAMES'),
    ('pxr.Usd.StageLoadRules.LoadAll', 'NAMES'),
    ('pxr.Usd.StageLoadRules.LoadAndUnload', 'NAMES'),
    ('pxr.Usd.StageLoadRules.LoadNone', 'NAMES'),
    ('pxr.Usd.StageLoadRules.LoadWithDescendants', 'NAMES'),
    ('pxr.Usd.StageLoadRules.LoadWithoutDescendants', 'NAMES'),
    ('pxr.Usd.StageLoadRules.Unload', 'NAMES'),
    ('pxr.Usd.UsdCollectionMembershipQuery.__init__', 'NAMES'),
    ('pxr.UsdAppUtils.FrameRecorder.SetRendererPlugin', 'INSTALLED'),
    ('pxr.UsdAppUtils.rendererArgs.GetAllPluginArguments', 'INSTALLED'),
    ('pxr.UsdAppUtils.rendererArgs.GetPluginIdFromArgument', 'INSTALLED'),
    ('pxr.UsdGeom.BBoxCache.__init__', 'NAMES'),
    ('pxr.UsdGeom.Boundable.ComputeExtentFromPlugins', 'INSTALLED'),
    ('pxr.UsdGeom.Camera.CreateShutterOpenAttr', 'NAMES'),
    ('pxr.UsdGeom.Camera.GetShutterOpenAttr', 'NAMES'),
    ('pxr.UsdGeom.Imageable.__init__', 'NAMES'),
    ('pxr.UsdGeom.XformCache.__init__', 'NAMES'),
    ('pxr.UsdImagingGL.Engine.GetRendererPlugins', 'INSTALLED'),
    ('pxr.UsdImagingGL.Engine.SetRendererPlugin', 'INSTALLED'),
    ('pxr.UsdLod.DistanceHeuristicQuery.__init__', 'NAMES'),
    ('pxr.UsdLod.HeuristicQuery.__init__', 'NAMES'),
    ('pxr.UsdLod.ScreenSizeHeuristicQuery.__init__', 'NAMES'),
    ('pxr.UsdLux.DomeLight.CreateTextureFileAttr', 'NAMES'),
    ('pxr.UsdLux.DomeLight.GetTextureFileAttr', 'NAMES'),
    ('pxr.UsdLux.DomeLight_1.CreateTextureFileAttr', 'NAMES'),
    ('pxr.UsdLux.DomeLight_1.GetTextureFileAttr', 'NAMES'),
    ('pxr.UsdLux.RectLight.CreateTextureFileAttr', 'NAMES'),
    ('pxr.UsdLux.RectLight.GetTextureFileAttr', 'NAMES'),
    ('pxr.UsdLux.ShapingAPI.CreateShapingIesFileAttr', 'NAMES'),
    ('pxr.UsdLux.ShapingAPI.GetShapingIesFileAttr', 'NAMES'),
    ('pxr.UsdMedia.SpatialAudio.CreateFilePathAttr', 'NAMES'),
    ('pxr.UsdMedia.SpatialAudio.GetFilePathAttr', 'NAMES'),
    ('pxr.UsdMtlx.MaterialXConfigAPI.__init__', 'NAMES'),
    ('pxr.UsdPhysics.LoadUsdPhysicsFromRange', 'MEMORY'),
    ('pxr.UsdPhysics.PhysicsCollectionMembershipQueryVector.__init__', 'NAMES'),
    ('pxr.UsdPhysics.UsdPhysicsLoadStageFromPrimRange', 'MEMORY'),
    ('pxr.UsdProfiles.ClaimsAPI.ClearProfileCompatibility', 'NAMES'),
    ('pxr.UsdProfiles.ClaimsAPI.GetCompatibleProfiles', 'NAMES'),
    ('pxr.UsdProfiles.ClaimsAPI.GetProfileExceptions', 'NAMES'),
    ('pxr.UsdProfiles.ClaimsAPI.GetProfilesInfo', 'NAMES'),
    ('pxr.UsdProfiles.ClaimsAPI.SetProfileCompatible', 'NAMES'),
    ('pxr.UsdProfiles.ClaimsAPI.SetProfileCompatibleWithExceptions', 'NAMES'),
    ('pxr.UsdProfiles.ClaimsAPI.SetProfilesInfo', 'NAMES'),
    ('pxr.UsdProfiles.ProfileRegistry.GetAllProfiles', 'NAMES'),
    ('pxr.UsdProfiles.ProfileRegistry.GetFileFormatImpliedCapabilities', 'NAMES'),
    ('pxr.UsdProfiles.ProfileRegistry.IsProfile', 'NAMES'),
    ('pxr.UsdProfiles.ProfileRegistry.__init__', 'NAMES'),
    ('pxr.UsdProfiles.ProfilesDot', 'MEMORY'),
    ('pxr.UsdProfiles.ProfilesMarkdown', 'MEMORY'),
    ('pxr.UsdProfiles.ProfilesMermaid', 'MEMORY'),
    ('pxr.UsdRender.Pass.CreateFileNameAttr', 'NAMES'),
    ('pxr.UsdRender.Pass.GetFileNameAttr', 'NAMES'),
    ('pxr.UsdRender.SettingsBase.CreateResolutionAttr', 'NAMES'),
    ('pxr.UsdRender.SettingsBase.GetResolutionAttr', 'NAMES'),
    ('pxr.UsdSemantics.LabelsQuery.__init__', 'NAMES'),
    ('pxr.UsdShade.Input.__init__', 'NAMES'),
    ('pxr.UsdShade.Output.__init__', 'NAMES'),
    ('pxr.UsdSkel.AnimQuery.__init__', 'NAMES'),
    ('pxr.UsdSkel.BakeSkinning', 'MEMORY'),
    ('pxr.UsdSkel.BlendShapeQuery.__init__', 'NAMES'),
    ('pxr.UsdSkel.Cache.__init__', 'NAMES'),
    ('pxr.UsdSkel.SkeletonQuery.__init__', 'NAMES'),
    ('pxr.UsdSkel.SkinningQuery.__init__', 'NAMES'),
    ('pxr.UsdUtils.ExtractUsdzPackage', 'PYTHON'),
    ('pxr.UsdUtils.GetMaterialsScopeName', 'NAMES'),
    ('pxr.UsdUtils.RegisteredVariantSet.SelectionExportPolicy', 'NAMES'),
    ('pxr.UsdUtils.SparseAttrValueWriter.__init__', 'NAMES'),
    ('pxr.UsdUtils.SparseValueWriter.GetSparseAttrValueWriters', 'NAMES'),
    ('pxr.UsdUtils.SparseValueWriter.__init__', 'NAMES'),
    ('pxr.UsdUtils.StageCache.__init__', 'NAMES'),
    ('pxr.UsdUtils.usdzUtils.UsdzUpdateIterator._WritePackage', 'PYTHON'),
    ('pxr.UsdValidation.ValidationRegistry.GetOrLoadAllValidatorSuites', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.GetOrLoadAllValidators', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.GetOrLoadValidatorByName', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.GetOrLoadValidatorSuiteByName', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.GetOrLoadValidatorSuitesByName', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.GetOrLoadValidatorsByName', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.GetValidatorMetadataForPlugin', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.GetValidatorMetadataForPlugins', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.RegisterPluginLayerValidator', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.RegisterPluginPrimValidator', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.RegisterPluginStageValidator', 'INSTALLED'),
    ('pxr.UsdValidation.ValidationRegistry.RegisterPluginValidatorSuite', 'INSTALLED'),
    ('pxr.UsdVol.VolumeFieldAsset.CreateFilePathAttr', 'NAMES'),
    ('pxr.UsdVol.VolumeFieldAsset.GetFilePathAttr', 'NAMES'),
    ('pxr.Vt.BoolArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.BoolArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.CharArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.CharArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.DoubleArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.DoubleArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.DualQuatdArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.DualQuatdArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.DualQuatfArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.DualQuatfArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.DualQuathArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.DualQuathArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.FloatArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.FloatArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.HalfArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.HalfArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Int64ArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Int64ArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.IntArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.IntArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.IntervalArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.IntervalArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Matrix2dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Matrix2dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Matrix2fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Matrix2fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Matrix3dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Matrix3dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Matrix3fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Matrix3fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Matrix4dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Matrix4dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Matrix4fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Matrix4fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.QuatdArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.QuatdArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.QuaternionArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.QuaternionArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.QuatfArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.QuatfArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.QuathArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.QuathArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Range1dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Range1dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Range1fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Range1fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Range2dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Range2dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Range2fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Range2fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Range3dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Range3dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Range3fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Range3fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Rect2iArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Rect2iArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.ShortArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.ShortArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.StringArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.StringArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.TimeCodeArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.TimeCodeArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.TokenArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.TokenArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.UCharArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.UCharArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.UInt64ArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.UInt64ArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.UIntArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.UIntArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.UShortArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.UShortArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec2dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec2dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec2fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec2fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec2hArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec2hArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec2iArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec2iArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec3dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec3dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec3fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec3fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec3hArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec3hArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec3iArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec3iArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec4dArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec4dArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec4fArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec4fArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec4hArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec4hArrayEditBuilder.WriteRef', 'NAMES'),
    ('pxr.Vt.Vec4iArrayEditBuilder.Write', 'NAMES'),
    ('pxr.Vt.Vec4iArrayEditBuilder.WriteRef', 'NAMES'),
)}
