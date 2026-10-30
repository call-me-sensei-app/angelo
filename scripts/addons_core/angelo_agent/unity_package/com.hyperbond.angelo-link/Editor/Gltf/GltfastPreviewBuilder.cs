// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The live preview's .glb loader, built on Unity's glTFast (com.unity.cloud.gltfast, Apache-2.0). This assembly
// compiles only when that package is installed (ANGELO_GLTFAST, a version define in its .asmdef); without it the
// link still opens, saves and re-imports, and the overlay says the preview needs glTFast.
//
// glTFast picks its materials for the project's render pipeline (Built-in, URP or HDRP) itself. The preview takes
// meshes only: lights and cameras would add to the scene's lighting or the Game view next to the hidden originals'
// own, which the preview can't switch off without changing serialized properties.

#if ANGELO_GLTFAST
using System.Threading.Tasks;
using GLTFast;
using UnityEditor;
using UnityEngine;

namespace Hyperbond.AngeloLink.Gltf
{
    [InitializeOnLoad]
    static class GltfastPreviewRegistration
    {
        static GltfastPreviewRegistration()
        {
            LivePreview.Builder = new GltfastPreviewBuilder();
        }
    }

    sealed class GltfastPreviewBuilder : IPreviewBuilder
    {
        public async Task<IPreviewModel> Load(byte[] glb)
        {
            // The whole load in one go: an editor tick has no frame budget to share, and a stale load is dropped.
            var import = new GltfImport(deferAgent: new UninterruptedDeferAgent());
            var settings = new ImportSettings { AnimationMethod = AnimationMethod.None };
            if (await import.LoadGltfBinary(glb, null, settings))
                return new Model(import);
            import.Dispose();
            return null;
        }

        sealed class Model : IPreviewModel
        {
            readonly GltfImport _import;

            public Model(GltfImport import) { _import = import; }

            public Task<bool> Instantiate(Transform parent)
            {
                var settings = new InstantiationSettings { Mask = ComponentType.Mesh };
                var instantiator = new GameObjectInstantiator(_import, parent, null, settings);
                return _import.InstantiateMainSceneAsync(instantiator);
            }

            public void Dispose() => _import.Dispose();   // Destroys the meshes, materials and textures it made.
        }
    }
}
#endif
