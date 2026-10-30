// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// What an asset is to the link in Unity: a model file under Assets/ whose kind Angelo edits, the asset a GameObject
// comes from (its nearest enclosing model-prefab instance), and every instance of an asset in the open scenes.
// A model file imported by Unity (ModelImporter for .fbx/.blend, glTFast's ScriptedImporter for .glb) is a Model
// Prefab, so PrefabUtility resolves scene objects back to it.

using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Hyperbond.AngeloLink
{
    public static class AssetRules
    {
        /// <summary>Kinds Angelo's Unity adapter admits (unity_link_assets.KINDS): .blend as itself, .glb and .fbx
        /// through a working .blend. Angelo checks again; this keeps the menus honest.</summary>
        public static readonly string[] Kinds = { ".blend", ".glb", ".fbx" };

        /// <summary>The Unity project folder (the one holding Assets/), absolute, '/' separators.</summary>
        public static string ProjectRoot => Path.GetDirectoryName(Application.dataPath)?.Replace('\\', '/') ?? "";

        public static bool IsEditable(string assetPath)
        {
            if (string.IsNullOrEmpty(assetPath) || !assetPath.StartsWith("Assets/"))
                return false;
            var extension = Path.GetExtension(assetPath).ToLowerInvariant();
            return System.Array.IndexOf(Kinds, extension) >= 0;
        }

        /// <summary>The link's asset name for a Unity asset path: the project path as Unity writes it
        /// ("Assets/Props/crate.fbx"), which Angelo's Unity adapter resolves against the project folder.</summary>
        public static string ToWire(string assetPath) => assetPath;

        public static string FromWire(string wire) => wire ?? "";

        /// <summary>The editable asset <paramref name="gameObject"/> comes from: the nearest enclosing prefab instance
        /// made from a model of one of <see cref="Kinds"/>, looking outward through nested prefabs; null when there is none.</summary>
        public static string AssetOf(GameObject gameObject)
        {
            var current = gameObject;
            while (current != null)
            {
                var root = PrefabUtility.GetNearestPrefabInstanceRoot(current);
                if (root == null)
                    return null;
                var path = PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(root);
                if (IsEditable(path))
                    return path;
                current = root.transform.parent != null ? root.transform.parent.gameObject : null;
            }
            return null;
        }

        /// <summary>The roots of every instance of <paramref name="assetPath"/> in the loaded scenes and the open
        /// prefab stage. Nested instances of the same asset are part of the outer one.</summary>
        public static List<GameObject> FindInstances(string assetPath)
        {
            var found = new List<GameObject>();
            var stack = new Stack<Transform>();
            foreach (var scene in OpenScenes())
                foreach (var root in scene.GetRootGameObjects())
                    stack.Push(root.transform);
            while (stack.Count > 0)
            {
                var transform = stack.Pop();
                var gameObject = transform.gameObject;
                if ((gameObject.hideFlags & HideFlags.DontSaveInEditor) != 0)
                    continue;   // Previews and other editor-only objects.
                if (PrefabUtility.GetNearestPrefabInstanceRoot(gameObject) == gameObject
                        && PrefabUtility.GetPrefabAssetPathOfNearestInstanceRoot(gameObject) == assetPath)
                {
                    found.Add(gameObject);
                    continue;
                }
                for (var i = 0; i < transform.childCount; i++)
                    stack.Push(transform.GetChild(i));
            }
            return found;
        }

        static IEnumerable<Scene> OpenScenes()
        {
            for (var i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (scene.isLoaded)
                    yield return scene;
            }
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null)
                yield return stage.scene;
        }

        /// <summary>The importer's scale factor for the live preview (ModelImporter's Scale Factor; 1 for other
        /// importers), so the preview matches what the re-import will show.</summary>
        public static float ImportScale(string assetPath)
        {
            return AssetImporter.GetAtPath(assetPath) is ModelImporter model ? model.globalScale : 1f;
        }
    }
}
