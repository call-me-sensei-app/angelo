/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Every pipeline library's file entry point (doc/angelo/script_confinement_pipeline.rst): where it is defined, how
 * it is held, and which arguments name files. `native_inventory.PIPELINE` (test_blender_confinement_pipeline)
 * classifies every entry point the bundled libraries expose against this table and calls each gated one with a
 * private path.
 *
 * USD reads and writes assets through the confined resolver (io/usd/usd_confined_resolver.hh) whatever the entry
 * point; its rows here are the entry points that open files outside Ar (the usdz writer, data files, reports), load
 * native code (Plug), or name a file directly (so the refusal is the confinement's own exception).
 */

#include "bpy_angelo_guard_confine_pipeline.hh"

namespace blender::angelo_guard {

#define F Kind::Function
#define M Kind::Method
#define S Kind::Static
#define C Kind::ClassMethod
#define I Kind::Init
#define N Kind::New
#define R(p, k) Arg{p, k, Role::Read}
#define W(p, k) Arg{p, k, Role::Write}
#define D(p, k) Arg{p, k, Role::Folders}
#define P(p, k) Arg{p, k, Role::Native}
#define O(p, k) Arg{p, k, Role::None}
#define AR Arg{-1, nullptr, Role::AnyRead}
#define AW Arg{-1, nullptr, Role::AnyWrite}
#define NONE Arg{-1, nullptr, Role::None}
#define G(module, owner, name, kind, a0, a1, a2, special) \
  Gate{module, owner, name, kind, {a0, a1, a2}, Special::special, nullptr, nullptr, nullptr, nullptr, nullptr}

Gate gates[] = {
    /* USD. */
    G("pxr.Usd._usd", "Stage", "Open", S, R(0, "filePath"), NONE, NONE, None),
    G("pxr.Usd._usd", "Stage", "OpenMasked", S, R(0, "filePath"), NONE, NONE, None),
    G("pxr.Usd._usd", "Stage", "CreateNew", S, W(0, "identifier"), NONE, NONE, None),
    G("pxr.Usd._usd", "Stage", "Export", M, W(0, "filename"), NONE, NONE, None),
    G("pxr.Sdf._sdf", "Layer", "FindOrOpen", S, R(0, "identifier"), NONE, NONE, None),
    G("pxr.Sdf._sdf", "Layer", "OpenAsAnonymous", S, R(0, "layerPath"), NONE, NONE, None),
    G("pxr.Sdf._sdf", "Layer", "CreateNew", S, W(0, "identifier"), NONE, NONE, None),
    G("pxr.Sdf._sdf", "Layer", "Export", M, W(0, "filename"), NONE, NONE, None),
    G("pxr.Sdf._sdf", "Layer", "Import", M, R(0, nullptr), NONE, NONE, None),
    G("pxr.Sdf._sdf", "Layer", "_WriteDataFile", M, W(0, nullptr), NONE, NONE, None),
    G("pxr.Sdf._sdf", "ZipFile", "Open", S, R(0, "filePath"), NONE, NONE, None),
    G("pxr.Sdf._sdf", "ZipFileWriter", "CreateNew", S, W(0, "filePath"), NONE, NONE, None),
    G("pxr.Sdf._sdf", "ZipFileWriter", "AddFile", M, R(0, "filePath"), NONE, NONE, None),
    G("pxr.Sdf._sdf", "CrateInfo", "Open", S, R(0, "fileName"), NONE, NONE, None),
    G("pxr.UsdUtils._usdUtils", nullptr, "CreateNewUsdzPackage", F, R(0, "assetPath"), W(1, "usdzFilePath"), NONE,
      None),
    G("pxr.UsdUtils._usdUtils", nullptr, "CreateNewARKitUsdzPackage", F, R(0, "assetPath"), W(1, "usdzFilePath"),
      NONE, None),
    G("pxr.UsdAppUtils._usdAppUtils", "FrameRecorder", "Record", M, W(3, "outputImagePath"), NONE, NONE, None),
    G("pxr.Glf._glf", "DrawTarget", "WriteToFile", M, W(1, "filename"), NONE, NONE, None),
    G("pxr.Tf._tf", nullptr, "TouchFile", F, W(0, "fileName"), NONE, NONE, None),
    G("pxr.Tf._tf", "MallocTag.CallTree", "Report", M, W(0, "fileName"), NONE, NONE, None),
    G("pxr.Tf._tf", "MallocTag.CallTree", "LoadReport", M, R(0, "fileName"), NONE, NONE, None),
    G("pxr.Tf._tf", "ScriptModuleLoader", "WriteDotFile", M, W(0, nullptr), NONE, NONE, None),
    G("pxr.Trace._trace", "Reporter", "Report", M, W(0, nullptr), NONE, NONE, None),
    G("pxr.Trace._trace", "Reporter", "ReportChromeTracingToFile", M, W(0, nullptr), NONE, NONE, None),
    G("pxr.Trace._trace", "Reporter", "LoadReport", S, R(0, "fileName"), NONE, NONE, None),
    G("pxr.UsdMtlx._usdMtlx", nullptr, "_TestFile", F, R(0, "pathname"), NONE, NONE, None),
    G("pxr.UsdProfiles._usdProfiles", "ProfileRegistry", "_TestLoadFromFile", S, R(0, "filePath"), NONE, NONE,
      None),
    G("pxr.Plug._plug", "Registry", "RegisterPlugins", M, NONE, NONE, NONE, Refuse),
    G("pxr.Sdr._sdr", nullptr, "FsHelpersDiscoverFiles", F, D(0, nullptr), NONE, NONE, None),

    /* OpenImageIO. */
    G("OpenImageIO.OpenImageIO", nullptr, "attribute", F, NONE, NONE, NONE, OiioAttribute),
    G("OpenImageIO.OpenImageIO", "ImageInput", "open", S, R(0, "filename"), NONE, NONE, None),
    G("OpenImageIO.OpenImageIO", "ImageInput", "create", S, R(0, "filename"), P(1, "plugin_searchpath"), NONE, None),
    G("OpenImageIO.OpenImageIO", "ImageInput", "valid_file", M, R(0, nullptr), NONE, NONE, None),
    G("OpenImageIO.OpenImageIO", "ImageOutput", "create", S, W(0, "filename"), P(1, "plugin_searchpath"), NONE,
      None),
    G("OpenImageIO.OpenImageIO", "ImageOutput", "open", M, W(0, "filename"), NONE, NONE, None),
    G("OpenImageIO.OpenImageIO", "ImageBuf", "__init__", I, R(0, "name"), NONE, NONE, OiioSharedName),
    G("OpenImageIO.OpenImageIO", "ImageBuf", "reset", M, R(0, "name"), NONE, NONE, OiioSharedName),
    G("OpenImageIO.OpenImageIO", "ImageBuf", "init_spec", M, R(0, "filename"), NONE, NONE, OiioSharedName),
    G("OpenImageIO.OpenImageIO", "ImageBuf", "write", M, W(0, "filename"), NONE, NONE, None),
    G("OpenImageIO.OpenImageIO", "ImageBufAlgo", "make_texture", S, R(1, "filename"), W(2, "outputfilename"), NONE,
      OiioSharedName),
    G("OpenImageIO.OpenImageIO", "ImageBufAlgo", "render_text", S, R(5, "fontname"), NONE, NONE, None),
    G("OpenImageIO.OpenImageIO", "ImageBufAlgo", "text_size", S, R(2, "fontname"), NONE, NONE, None),
    G("OpenImageIO.OpenImageIO", "ImageBufAlgo", "ociofiletransform", S, R(2, "name"), NONE, NONE, OiioLut),
    G("OpenImageIO.OpenImageIO", "ImageBufAlgo", "colorconvert", S, NONE, NONE, NONE, OiioColorConfig),
    G("OpenImageIO.OpenImageIO", "ImageBufAlgo", "ociodisplay", S, NONE, NONE, NONE, OiioColorConfig),
    G("OpenImageIO.OpenImageIO", "ImageBufAlgo", "ociolook", S, NONE, NONE, NONE, OiioColorConfig),
    G("OpenImageIO.OpenImageIO", "ImageBufAlgo", "ocionamedtransform", S, NONE, NONE, NONE, OiioColorConfig),
    G("OpenImageIO.OpenImageIO", "ColorConfig", "__init__", I, R(0, "filename"), NONE, NONE, OiioColorConfig),
    G("OpenImageIO.OpenImageIO", "ColorConfig", "default_colorconfig", S, NONE, NONE, NONE, OiioColorConfig),
    G("OpenImageIO.OpenImageIO", "ImageCache", "attribute", M, NONE, NONE, NONE, OiioCacheAttribute),
    G("OpenImageIO.OpenImageIO", "ImageCache", "get_imagespec", M, R(0, "filename"), NONE, NONE, OiioCacheName),
    G("OpenImageIO.OpenImageIO", "ImageCache", "get_pixels", M, R(0, "filename"), NONE, NONE, OiioCacheName),
    G("OpenImageIO.OpenImageIO", "ImageCache", "get_cache_dimensions", M, R(0, "filename"), NONE, NONE,
      OiioCacheName),
    G("OpenImageIO.OpenImageIO", "TextureSystem", "attribute", M, NONE, NONE, NONE, OiioCacheAttribute),
    G("OpenImageIO.OpenImageIO", "TextureSystem", "texture", M, R(0, "filename"), NONE, NONE, OiioCacheName),
    G("OpenImageIO.OpenImageIO", "TextureSystem", "texture3d", M, R(0, "filename"), NONE, NONE, OiioCacheName),
    G("OpenImageIO.OpenImageIO", "TextureSystem", "environment", M, R(0, "filename"), NONE, NONE, OiioCacheName),
    G("OpenImageIO.OpenImageIO", "TextureSystem", "imagespec", M, R(0, "filename"), NONE, NONE, OiioCacheName),
    G("OpenImageIO.OpenImageIO", "TextureSystem", "inventory_udim", M, R(0, "filename"), NONE, NONE, OiioCacheName),

    /* OpenVDB. */
    G("openvdb", nullptr, "read", F, R(0, "filename"), NONE, NONE, None),
    G("openvdb", nullptr, "readAll", F, R(0, "filename"), NONE, NONE, None),
    G("openvdb", nullptr, "readAllGridMetadata", F, R(0, "filename"), NONE, NONE, None),
    G("openvdb", nullptr, "readGridMetadata", F, R(0, "filename"), NONE, NONE, None),
    G("openvdb", nullptr, "readMetadata", F, R(0, "filename"), NONE, NONE, None),
    G("openvdb", nullptr, "write", F, W(0, "filename"), NONE, NONE, None),

    /* MaterialX: readers judge their file against the search path, and their XIncludes. */
    G("MaterialX.PyMaterialXFormat", nullptr, "readFromXmlFileBase", F, R(1, "filename"), O(2, "searchPath"),
      O(3, "readOptions"), MxRead),
    G("MaterialX.PyMaterialXFormat", nullptr, "readFromXmlString", F, NONE, O(2, "searchPath"), O(3, "readOptions"),
      MxXInclude),
    G("MaterialX.PyMaterialXFormat", nullptr, "loadLibrary", F, R(0, "file"), O(2, "searchPath"),
      O(3, "readOptions"), MxRead),
    G("MaterialX.PyMaterialXFormat", nullptr, "loadLibraries", F, D(0, "libraryFolders"), O(1, "searchPath"),
      O(4, "readOptions"), MxRead),
    G("MaterialX.PyMaterialXFormat", nullptr, "loadDocuments", F, R(0, "rootPath"), O(1, "searchPath"),
      O(6, "readOptions"), MxRead),
    G("MaterialX.PyMaterialXFormat", nullptr, "writeToXmlFile", F, W(1, "filename"), NONE, NONE, None),
    G("MaterialX.PyMaterialXFormat", nullptr, "readFile", F, R(0, nullptr), NONE, NONE, None),
    G("MaterialX.PyMaterialXFormat", "FilePath", "createDirectory", M, W(-1, nullptr), NONE, NONE, MxSelf),
    G("MaterialX.PyMaterialXFormat", "FilePath", "getFilesInDirectory", M, R(-1, nullptr), NONE, NONE, MxSelf),
    G("MaterialX.PyMaterialXFormat", "FilePath", "getSubDirectories", M, R(-1, nullptr), NONE, NONE, MxSelf),
    G("MaterialX.PyMaterialXRender", "*", "acquireImage", M, R(0, "filePath"), NONE, NONE, None),
    G("MaterialX.PyMaterialXRender", "*", "loadImage", M, R(0, nullptr), NONE, NONE, None),
    G("MaterialX.PyMaterialXRender", "*", "saveImage", M, W(0, nullptr), NONE, NONE, None),
    G("MaterialX.PyMaterialXRender", "*", "loadGeometry", M, R(0, nullptr), NONE, NONE, None),
    G("MaterialX.PyMaterialXRender", "*", "load", M, R(0, nullptr), NONE, NONE, None),
    G("MaterialX.PyMaterialXRender", "ImageHandler", "setSearchPath", M, D(0, nullptr), NONE, NONE, None),
    G("MaterialX.PyMaterialXGenShader", "GenContext", "registerSourceCodeSearchPath", M, D(0, nullptr), NONE, NONE,
      None),

    /* OpenColorIO. */
    G("PyOpenColorIO.PyOpenColorIO", "Config", "CreateFromFile", S, R(0, "fileName"), NONE, NONE, None),
    G("PyOpenColorIO.PyOpenColorIO", "Config", "CreateFromEnv", S, NONE, NONE, NONE, OcioEnv),
    G("PyOpenColorIO.PyOpenColorIO", nullptr, "GetCurrentConfig", F, NONE, NONE, NONE, OcioEnv),
    G("PyOpenColorIO.PyOpenColorIO", nullptr, "SetCurrentConfig", F, NONE, NONE, NONE, OcioSetCurrent),
    G("PyOpenColorIO.PyOpenColorIO", "ConfigMerger", "CreateFromFile", S, R(0, "filepath"), NONE, NONE, None),
    G("PyOpenColorIO.PyOpenColorIO", "CDLTransform", "CreateFromFile", S, R(0, "src"), NONE, NONE, None),
    G("PyOpenColorIO.PyOpenColorIO", "CDLTransform", "CreateGroupFromFile", S, R(0, "src"), NONE, NONE, None),
    G("PyOpenColorIO.PyOpenColorIO", "Config", "serialize", M, W(0, "fileName"), NONE, NONE, None),
    G("PyOpenColorIO.PyOpenColorIO", "Config", "archive", M, W(0, nullptr), NONE, NONE, None),
    G("PyOpenColorIO.PyOpenColorIO", "Config", "instantiateDisplayFromICCProfile", M,
      R(0, "ICCProfileFilepath"), NONE, NONE, None),
    G("PyOpenColorIO.PyOpenColorIO", nullptr, "ExtractOCIOZArchive", F, R(0, nullptr), W(1, nullptr), NONE, None),
    G("PyOpenColorIO.PyOpenColorIO", "Config", "getProcessor", M, NONE, NONE, NONE, OcioProcessor),
    G("PyOpenColorIO.PyOpenColorIO", "Config", "GetProcessorFromConfigs", S, NONE, NONE, NONE, OcioProcessor),
    G("PyOpenColorIO.PyOpenColorIO", "Config", "GetProcessorFromBuiltinColorSpace", S, NONE, NONE, NONE,
      OcioProcessor),
    G("PyOpenColorIO.PyOpenColorIO", "Config", "GetProcessorToBuiltinColorSpace", S, NONE, NONE, NONE,
      OcioProcessor),
    G("PyOpenColorIO.PyOpenColorIO", "MixingColorSpaceManager", "__init__", I, NONE, NONE, NONE, OcioProcessor),
    G("PyOpenColorIO.PyOpenColorIO", "Baker", "bake", M, W(0, "fileName"), NONE, NONE, OcioProcessor),
    G("PyOpenColorIO.PyOpenColorIO", "GroupTransform", "write", M, W(1, "fileName"), NONE, NONE, OcioProcessor),
    G("PyOpenColorIO.PyOpenColorIO", "LegacyViewingPipeline", "getProcessor", M, NONE, NONE, NONE, OcioProcessor),
    G("PyOpenColorIO.PyOpenColorIO", "DisplayViewHelpers", "GetProcessor", S, NONE, NONE, NONE, OcioProcessor),

    /* OpenEXR. */
    G("OpenEXR", "File", "__init__", I, R(0, "filename"), NONE, NONE, None),
    G("OpenEXR", "File", "write", M, W(0, "filename"), NONE, NONE, None),
    G("OpenEXR", "InputFile", "__init__", I, R(0, nullptr), NONE, NONE, None),
    G("OpenEXR", "OutputFile", "__init__", I, W(0, nullptr), NONE, NONE, None),
    G("OpenEXR", "InputFile", "__new__", N, R(0, nullptr), NONE, NONE, None),
    G("OpenEXR", "OutputFile", "__new__", N, W(0, nullptr), NONE, NONE, None),
    G("OpenEXR", nullptr, "isOpenExrFile", F, R(0, nullptr), NONE, NONE, None),

    /* OpenTimelineIO: its adapters are Python (audited); the core's JSON files are native. */
    G("opentimelineio._otio", "SerializableObject", "to_json_file", M, W(0, "file_name"), NONE, NONE, None),
    G("opentimelineio._otio", "SerializableObject", "from_json_file", S, R(0, "file_name"), NONE, NONE, None),
    G("opentimelineio._otio", nullptr, "deserialize_json_from_file", F, R(0, "filename"), NONE, NONE, None),
    G("opentimelineio._otio", nullptr, "_serialize_json_to_file", F, W(1, "filename"), NONE, NONE, None),

    /* OSL queries: a shader name looked up in a search path. */
    G("oslquery.oslquery", "OSLQuery", "__init__", I, R(0, "shadername"), D(1, "searchpath"), NONE, SearchName),
    G("oslquery.oslquery", "OSLQuery", "open", M, R(0, "shadername"), D(1, "searchpath"), NONE, SearchName),

    /* Audaspace. */
    G("aud", "Sound", "__new__", N, R(0, "filename"), NONE, NONE, None),
    G("aud", "Sound", "file", C, R(0, "filename"), NONE, NONE, None),
    G("aud", "Sound", "write", M, W(0, "filename"), NONE, NONE, None),
    G("aud", "HRTF", "loadLeftHrtfSet", M, D(1, "directory"), NONE, NONE, None),
    G("aud", "HRTF", "loadRightHrtfSet", M, D(1, "directory"), NONE, NONE, None),

    /* Mantaflow: arguments by name or position, signatures unpublished: every string is a path. */
    G("manta", nullptr, "save", F, AW, NONE, NONE, None),
    G("manta", nullptr, "load", F, AR, NONE, NONE, None),
    G("manta", nullptr, "getUniFileSize", F, AR, NONE, NONE, None),
    G("manta", nullptr, "getNpzFileSize", F, AR, NONE, NONE, None),
    G("manta", nullptr, "printUniFileInfoString", F, AR, NONE, NONE, None),
    G("manta", "*", "save", M, AW, NONE, NONE, None),
    G("manta", "*", "saveMean", M, AW, NONE, NONE, None),
    G("manta", "*", "load", M, AR, NONE, NONE, None),
    G("manta", "*", "readParticles", M, AR, NONE, NONE, None),
};

const size_t gate_count = sizeof(gates) / sizeof(gates[0]);

}  // namespace blender::angelo_guard
