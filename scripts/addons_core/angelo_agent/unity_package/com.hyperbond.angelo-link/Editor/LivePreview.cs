// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Shows Angelo's live export in place of an asset's instances in the open scenes, without changing a scene.
//
// Each instance gets a root-level preview GameObject created HideAndDontSave (not saved, not in the Hierarchy, not
// editable) that follows the instance's transform; it is never parented under the instance, so the prefab instance
// gains no override. The instance's own renderers are hidden with Renderer.forceRenderingOff, a runtime switch that
// is not serialized. The .glb is built by an IPreviewBuilder (the glTFast assembly registers one when the package
// com.unity.cloud.gltfast is installed); one load is instantiated once per instance. A new export replaces the
// previews only once it is built, so originals never flash. Clear() and RestoreAsset() put everything back.

using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Hyperbond.AngeloLink
{
    /// <summary>A loaded .glb that can be instantiated under a parent any number of times.</summary>
    public interface IPreviewModel : IDisposable
    {
        Task<bool> Instantiate(Transform parent);
    }

    /// <summary>Builds .glb bytes into an IPreviewModel; null when the bytes can't be read.</summary>
    public interface IPreviewBuilder
    {
        Task<IPreviewModel> Load(byte[] glb);
    }

    public sealed class LivePreview
    {
        public const string PreviewName = "AngeloLivePreview";

        /// <summary>Set by the glTFast assembly at load; null when glTFast isn't installed.</summary>
        public static IPreviewBuilder Builder;

        public string Asset { get; private set; } = "";   // Unity asset path of the linked asset.
        public long Version { get; private set; }
        public string LastError { get; private set; } = "";
        public bool HasExport => _glb != null;
        public int Count => _entries.Count;

        sealed class Entry
        {
            public GameObject Root;
            public List<Renderer> Hidden = new List<Renderer>();
        }

        readonly Dictionary<GameObject, Entry> _entries = new Dictionary<GameObject, Entry>();
        IPreviewModel _model;
        byte[] _glb;
        int _generation;

        /// <summary>A new export from Angelo: rebuild every instance's preview from it.</summary>
        public void Show(string asset, long version, byte[] glb)
        {
            if (asset != Asset)
                Clear();
            Asset = asset;
            Version = version;
            _glb = glb;
            Apply();
        }

        /// <summary>Put the current export on the open scenes' instances (a new export, a scene opened, a rescan).
        /// Asynchronous: the previews swap in once built; a newer call supersedes an unfinished one.</summary>
        public async void Apply()
        {
            var generation = ++_generation;
            if (_glb == null || Asset == "")
                return;
            if (Builder == null)
            {
                LastError = "The live preview needs the glTFast package (com.unity.cloud.gltfast).";
                return;
            }
            IPreviewModel model;
            try
            {
                model = await Builder.Load(_glb);
            }
            catch (Exception error)
            {
                LastError = "Unity couldn't read Angelo's export: " + error.Message;
                return;
            }
            if (generation != _generation)
            {
                model?.Dispose();
                return;
            }
            if (model == null)
            {
                LastError = "Unity couldn't read Angelo's export.";
                return;
            }
            var built = new Dictionary<GameObject, Entry>();
            var scale = AssetRules.ImportScale(Asset);
            foreach (var instance in AssetRules.FindInstances(Asset))
            {
                var root = EditorUtility.CreateGameObjectWithHideFlags(PreviewName, HideFlags.HideAndDontSave);
                if (root.scene != instance.scene)
                    SceneManager.MoveGameObjectToScene(root, instance.scene);   // Prefab Mode, multi-scene editing.
                Place(root.transform, instance.transform, scale);
                bool ok;
                try
                {
                    ok = await model.Instantiate(root.transform);
                }
                catch (Exception error)
                {
                    LastError = "Unity couldn't build Angelo's export: " + error.Message;
                    ok = false;
                }
                MarkPreview(root);
                if (!ok || generation != _generation || instance == null)
                {
                    UnityEngine.Object.DestroyImmediate(root);
                    if (!ok || generation != _generation)
                    {
                        foreach (var entry in built.Values)
                            UnityEngine.Object.DestroyImmediate(entry.Root);
                        model.Dispose();
                        return;
                    }
                    continue;
                }
                built[instance] = new Entry { Root = root };
            }
            Swap(built, model);
            SceneView.RepaintAll();
        }

        void Swap(Dictionary<GameObject, Entry> built, IPreviewModel model)
        {
            foreach (var pair in _entries)
            {
                if (pair.Value.Root != null)
                    UnityEngine.Object.DestroyImmediate(pair.Value.Root);
                if (built.TryGetValue(pair.Key, out var next))
                    next.Hidden = pair.Value.Hidden;   // Still hidden; the new preview takes over.
                else
                    Unhide(pair.Value.Hidden);
            }
            _entries.Clear();
            foreach (var pair in built)
            {
                Hide(pair.Key, pair.Value.Hidden);
                _entries[pair.Key] = pair.Value;
            }
            _model?.Dispose();   // Its meshes, materials and textures go with the old previews.
            _model = model;
            LastError = "";
        }

        /// <summary>Every editor tick: follow the instances' transforms; drop previews whose instance is gone.
        /// Returns false when the set of instances may have changed (the caller rescans).</summary>
        public bool Follow()
        {
            if (_entries.Count == 0)
                return true;
            var scale = AssetRules.ImportScale(Asset);
            var intact = true;
            foreach (var pair in new List<KeyValuePair<GameObject, Entry>>(_entries))
            {
                if (pair.Key == null || pair.Value.Root == null)
                {
                    if (pair.Value.Root != null)
                        UnityEngine.Object.DestroyImmediate(pair.Value.Root);
                    Unhide(pair.Value.Hidden);
                    _entries.Remove(pair.Key);
                    intact = false;
                    continue;
                }
                Place(pair.Value.Root.transform, pair.Key.transform, scale);
            }
            return intact;
        }

        /// <summary>True when the open scenes' instances of the asset differ from the ones showing a preview.</summary>
        public bool InstancesChanged()
        {
            if (_glb == null || Asset == "")
                return false;
            var current = AssetRules.FindInstances(Asset);
            if (current.Count != _entries.Count)
                return true;
            foreach (var instance in current)
                if (!_entries.ContainsKey(instance))
                    return true;
            return false;
        }

        /// <summary>Remove the previews but keep the export (Play Mode, a domain reload); Apply() brings them back.</summary>
        public void Suspend()
        {
            _generation++;
            foreach (var entry in _entries.Values)
            {
                if (entry.Root != null)
                    UnityEngine.Object.DestroyImmediate(entry.Root);
                Unhide(entry.Hidden);
            }
            _entries.Clear();
            _model?.Dispose();
            _model = null;
        }

        /// <summary>Remove every preview and forget the export: the instances show the project's asset again.</summary>
        public void Clear()
        {
            Suspend();
            _glb = null;
            Asset = "";
            Version = 0;
        }

        /// <summary>Show every renderer of the asset's instances (after a re-import or a reload, when renderers this
        /// object didn't track may still carry forceRenderingOff from an earlier preview).</summary>
        public static void RestoreAsset(string asset)
        {
            if (string.IsNullOrEmpty(asset))
                return;
            foreach (var instance in AssetRules.FindInstances(asset))
                foreach (var renderer in instance.GetComponentsInChildren<Renderer>(true))
                    renderer.forceRenderingOff = false;
        }

        /// <summary>Destroy preview objects left by an earlier domain (their owner's state is gone).</summary>
        public static int DestroyLeftovers()
        {
            var removed = 0;
            foreach (var gameObject in Resources.FindObjectsOfTypeAll<GameObject>())
            {
                if (gameObject != null && gameObject.name == PreviewName && gameObject.transform.parent == null
                        && (gameObject.hideFlags & HideFlags.DontSaveInEditor) != 0)
                {
                    UnityEngine.Object.DestroyImmediate(gameObject);
                    removed++;
                }
            }
            return removed;
        }

        static void Place(Transform preview, Transform instance, float scale)
        {
            preview.SetPositionAndRotation(instance.position, instance.rotation);
            preview.localScale = instance.lossyScale * scale;
        }

        static void MarkPreview(GameObject root)
        {
            foreach (var transform in root.GetComponentsInChildren<Transform>(true))
            {
                transform.gameObject.hideFlags = HideFlags.HideAndDontSave;
                foreach (var component in transform.GetComponents<Component>())
                    if (component != null)
                        component.hideFlags = HideFlags.HideAndDontSave;
            }
        }

        static void Hide(GameObject instance, List<Renderer> hidden)
        {
            foreach (var renderer in instance.GetComponentsInChildren<Renderer>(true))
            {
                if (!renderer.forceRenderingOff)
                {
                    renderer.forceRenderingOff = true;
                    hidden.Add(renderer);
                }
            }
        }

        static void Unhide(List<Renderer> hidden)
        {
            foreach (var renderer in hidden)
                if (renderer != null)
                    renderer.forceRenderingOff = false;
            hidden.Clear();
        }
    }
}
