Angelo's live web preview: three.js and the libraries it loads
==============================================================

The live web preview (File > Export > Web, Live Web Preview) serves a page that draws the scene with three.js 0.185.1
(the npm package "three"), unmodified. Angelo bundles the files that page loads:

  macOS:    Angelo.app/Contents/Resources/5.2/scripts/addons_core/angelo_agent/web_preview/vendor/three/
  Windows:  5.2\scripts\addons_core\angelo_agent\web_preview\vendor\three\ in the install folder

MANIFEST in that folder lists each file's SHA-256. Besides three.js's own code, three.js ships five libraries prebuilt
in its examples/jsm/libs, each under its own license. Each component's license, and its NOTICE where the component
publishes one, is in the folder named after it here:

  three.js/LICENSE.txt               three.js (MIT), the LICENSE the npm package carries
  draco/LICENSE.txt                  Draco (Apache-2.0), github.com/google/draco (no NOTICE file)
  basis_universal/LICENSE.txt        Basis Universal (Apache-2.0), github.com/BinomialLLC/basis_universal
  basis_universal/NOTICE.txt         its NOTICE, which Apache-2.0 section 4(d) requires to be passed on
  meshoptimizer/LICENSE.txt          meshoptimizer's decoder (MIT), github.com/zeux/meshoptimizer
  ktx-parse/LICENSE.txt              KTX-Parse (MIT), github.com/donmccurdy/KTX-Parse
  zstddec/LICENSE.txt                zstddec (MIT), github.com/donmccurdy/zstddec, with the BSD license of the
                                     Zstandard decoder it contains (github.com/facebook/zstd)

three.js's own README for each library links its license in the project's repository; the texts here are those
repositories' license files (read 2026-10-25), byte for byte. The decoders are compiled WebAssembly with their
JavaScript wrappers, as each project publishes them; Angelo changes none of these files.

Angelo bundles no gltfpack. Web export uses one only when the person has installed it themselves (the glTF add-on's
gltfpack path, or a gltfpack on PATH).

Files, by component (paths in vendor/three/; tests/python/angelo_agent/test_native_web_preview_notices.py checks that
every bundled file is listed here exactly once)
-----------------------------------------------------------------------------------------------------------------

three.js (three.js/): MIT
    LICENSE
    build/three.core.min.js
    build/three.module.min.js
    examples/jsm/controls/OrbitControls.js
    examples/jsm/environments/RoomEnvironment.js
    examples/jsm/loaders/DRACOLoader.js
    examples/jsm/loaders/GLTFLoader.js
    examples/jsm/loaders/KTX2Loader.js
    examples/jsm/math/ColorSpaces.js
    examples/jsm/utils/BufferGeometryUtils.js
    examples/jsm/utils/SkeletonUtils.js
    examples/jsm/utils/WorkerPool.js
Draco (draco/): Apache-2.0
    examples/jsm/libs/draco/gltf/draco_decoder.wasm
    examples/jsm/libs/draco/gltf/draco_wasm_wrapper.js
Basis Universal (basis_universal/): Apache-2.0
    examples/jsm/libs/basis/basis_transcoder.js
    examples/jsm/libs/basis/basis_transcoder.wasm
meshoptimizer (meshoptimizer/): MIT
    examples/jsm/libs/meshopt_decoder.module.js
KTX-Parse (ktx-parse/): MIT
    examples/jsm/libs/ktx-parse.module.js
zstddec (zstddec/): MIT AND BSD-3-Clause
    examples/jsm/libs/zstddec.module.js
Angelo (none): the file list
    MANIFEST
