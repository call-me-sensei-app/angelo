// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Angelo Link for Unity: "Edit in Angelo" on a .blend, .glb or .fbx model (Project window, Hierarchy, the Scene view's
// Angelo Link overlay). Angelo asks the person before it opens the asset; then every change made there shows live on
// the asset's instances in the open scenes (LivePreview), and saving in Angelo updates the asset file, which this
// package re-imports (AssetDatabase.ImportAsset), replacing the live preview with the real asset. Nothing is written
// into a scene. The contract is doc/angelo/unity_link.rst; Angelo's side is shared with the Godot plugin.
//
// Everything here runs on the editor's main thread from EditorApplication.update; the socket never blocks it.

using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Hyperbond.AngeloLink
{
    [InitializeOnLoad]
    public static class AngeloLink
    {
        public const string PluginVersion = "0.1.0";
        const string LinkedKey = "Hyperbond.AngeloLink.Linked";   // SessionState: survives domain reloads.
        const double RescanSeconds = 0.5;

        public static LinkClient Client { get; private set; }
        public static LivePreview Preview { get; } = new LivePreview();
        public static string Linked { get; private set; } = "";   // Asset path linked to Angelo, or "".
        public static string Waiting { get; private set; } = "";  // Asset path of an unanswered request.
        public static string LastMessage { get; private set; } = "";   // The newest notice (tests read it).

        static int _nextId = 1;
        static bool _rescan;
        static double _lastRescan;
        static Matrix4x4? _modelRoot;   // The linked model's root transform as last imported (KeepPlacement).

        static AngeloLink()
        {
            Client = new LinkClient(ClientInfo());
            Client.Message += OnMessage;
            EditorApplication.update += Update;
            EditorApplication.hierarchyChanged += () => _rescan = true;
            EditorApplication.playModeStateChanged += OnPlayMode;
            EditorApplication.quitting += OnQuitting;
            EditorSceneManager.sceneOpened += (_, __) => _rescan = true;
            AssemblyReloadEvents.beforeAssemblyReload += OnBeforeReload;
            LivePreview.DestroyLeftovers();
            var linked = SessionState.GetString(LinkedKey, "");
            if (linked != "" && !EditorApplication.isPlayingOrWillChangePlaymode)
            {
                LivePreview.RestoreAsset(linked);
                EditAsset(linked, "");   // Rejoin after a domain reload: Angelo answers "opened" and re-exports.
            }
        }

        static Dictionary<string, object> ClientInfo()
        {
            return new Dictionary<string, object>
            {
                ["engine"] = LinkProtocol.Engine, ["unity"] = Application.unityVersion,
                ["project"] = AssetRules.ProjectRoot, ["project_name"] = Application.productName,
                ["plugin"] = PluginVersion,
            };
        }

        // What the person does --------------------------------------------------------------------------------

        public static void EditGameObject(GameObject gameObject)
        {
            var asset = AssetRules.AssetOf(gameObject);
            if (asset == null)
            {
                Notify(gameObject.name + " isn't from a .blend, .glb or .fbx file. Angelo edits models imported from those files.", true);
                return;
            }
            EditAsset(asset, gameObject.name);
        }

        public static void EditAsset(string asset, string nodeName)
        {
            if (!Client.EnsureConnected())
            {
                Notify(Client.LastError, true);
                return;
            }
            Waiting = asset;
            Client.Request(new Dictionary<string, object>
            {
                ["type"] = "open", ["id"] = NewId(), ["project"] = AssetRules.ProjectRoot,
                ["project_name"] = Application.productName, ["asset"] = AssetRules.ToWire(asset), ["node"] = nodeName,
            });
        }

        public static void StopLink()
        {
            if (Linked != "" && Client.IsReady)
                Client.Request(new Dictionary<string, object>
                {
                    ["type"] = "close", ["id"] = NewId(), ["project"] = AssetRules.ProjectRoot,
                    ["asset"] = AssetRules.ToWire(Linked),
                });
            SetLinked("");
            Waiting = "";
            Preview.Clear();
        }

        public static string StatusText()
        {
            if (Linked != "")
                return "Live: " + System.IO.Path.GetFileName(Linked);
            if (Waiting != "")
                return "Waiting for Angelo…";
            return Client.IsReady ? "Connected to Angelo" : "Not connected";
        }

        // The editor loop ----------------------------------------------------------------------------------------

        static void Update()
        {
            Client.Poll();
            if (!Preview.Follow())
                _rescan = true;
            if (_rescan && EditorApplication.timeSinceStartup - _lastRescan > RescanSeconds)
            {
                _rescan = false;
                _lastRescan = EditorApplication.timeSinceStartup;
                if (Preview.InstancesChanged())
                    Preview.Apply();
            }
        }

        static void OnPlayMode(PlayModeStateChange change)
        {
            if (change == PlayModeStateChange.ExitingEditMode)
                Preview.Suspend();   // Play Mode shows the project's asset; nothing of the preview enters it.
            else if (change == PlayModeStateChange.EnteredEditMode)
                Preview.Apply();
        }

        static void OnBeforeReload()
        {
            Preview.Suspend();   // This domain's objects go; the next one rejoins from SessionState.
            Client.Close();
        }

        static void OnQuitting()
        {
            if (Linked != "" && Client.IsReady)
            {
                StopLink();
                Client.Poll();
            }
            Client.Close();
        }

        // Angelo's messages --------------------------------------------------------------------------------------

        static void OnMessage(Dictionary<string, object> header, byte[] payload)
        {
            var asset = AssetRules.FromWire(LinkProtocol.Text(header, "asset"));
            var name = System.IO.Path.GetFileName(asset);
            switch (LinkProtocol.Text(header, "type"))
            {
                case "pending":
                    Notify("Angelo is asking whether to open " + name + ".", false);
                    BringAngeloForward();
                    break;
                case "opened":
                    SetLinked(asset);
                    _modelRoot = ModelRoot(asset);
                    Waiting = "";
                    Notify(name + " is open in Angelo. Changes made there show here live.", false);
                    break;
                case "refused":
                    if (LinkProtocol.Text(header, "code") != "superseded")
                        Notify(LinkProtocol.Text(header, "message", "Angelo didn't open the asset."), true);
                    Waiting = "";
                    break;
                case "scene":
                    if (asset == Linked)
                    {
                        Preview.Show(asset, LinkProtocol.Number(header, "version"), payload);
                        if (Preview.LastError != "")
                            Notify(Preview.LastError, true);
                    }
                    break;
                case "saved":
                    if (asset == Linked)
                        AssetDatabase.ImportAsset(asset, ImportAssetOptions.ForceUpdate);   // Re-import now.
                    break;
                case "ended":
                    if (asset == Linked)
                    {
                        SetLinked("");
                        Preview.Clear();   // A Stop asked here already unlinked, so this is Angelo's own end.
                        Notify("Angelo stopped editing " + name + ".", false);
                    }
                    break;
                case "status":
                    Notify(LinkProtocol.Text(header, "message"), LinkProtocol.Text(header, "code").EndsWith("failed"));
                    break;
            }
        }

        /// <summary>From the asset postprocessor: the linked asset was re-imported, so its instances show the saved
        /// asset; the next change in Angelo brings a new preview. Angelo hears "reimported".</summary>
        internal static void OnImported(string[] imported)
        {
            if (Linked == "" || System.Array.IndexOf(imported, Linked) < 0)
                return;
            Preview.Clear();   // Forget the export too, or the next rescan would put the old preview back.
            LivePreview.RestoreAsset(Linked);
            var asset = Linked;
            var placements = Placements(asset);   // Now, while the instances still have the old root.
            EditorApplication.delayCall += () => KeepPlacement(asset, placements);   // Unity rebuilds them after this.
            if (Client.IsReady)
                Client.Request(new Dictionary<string, object>
                {
                    ["type"] = "status", ["code"] = "reimported", ["asset"] = AssetRules.ToWire(Linked),
                });
        }

        /// <summary>Keep every instance of the re-imported model where it was when the model's root changed. A model
        /// file with one object imports with that object as the prefab root, and each scene instance's root transform
        /// holds the object's own transform (Blender's axis rotation included) as an override. When an edit adds a
        /// second object, the model gets a new, empty root and the old object becomes its child, so the instances'
        /// overrides would turn and move the whole model, while the values they don't override follow the new root.
        /// So each instance's placement relative to the old root is taken before Unity rebuilds the instances
        /// (<see cref="Placements"/>), and the rebuilt root is set to that placement times the new root: every object
        /// stays where it was. One Undo step; the scene is left to the person to save.</summary>
        static void KeepPlacement(string asset, Dictionary<Object, Matrix4x4> placements)
        {
            var after = ModelRoot(asset);
            if (placements == null || after == null)
            {
                _modelRoot = after;
                return;
            }
            _modelRoot = after;
            var moved = 0;
            foreach (var instance in AssetRules.FindInstances(asset))
            {
                var handle = PrefabUtility.GetPrefabInstanceHandle(instance);
                if (handle == null || !placements.TryGetValue(handle, out var placement))
                    continue;
                var placed = placement * after.Value;
                var transform = instance.transform;
                Undo.RecordObject(transform, "Keep " + System.IO.Path.GetFileName(asset) + " in place");
                transform.localPosition = placed.GetColumn(3);
                transform.localRotation = placed.rotation;
                transform.localScale = placed.lossyScale;
                moved++;
            }
            if (moved > 0)
                Notify(System.IO.Path.GetFileName(asset) + " has a new root object; its " + moved
                       + " instances kept their place. Save the scene to keep that.", false);
        }

        /// <summary>{prefab instance handle: the instance's placement relative to the model's root} when the model's root
        /// is about to change (the re-import of Angelo's save gave it another root transform), else null.</summary>
        static Dictionary<Object, Matrix4x4> Placements(string asset)
        {
            var before = _modelRoot;
            var after = ModelRoot(asset);
            if (before == null || after == null || before.Value == after.Value)
                return null;
            var placements = new Dictionary<Object, Matrix4x4>();
            foreach (var instance in AssetRules.FindInstances(asset))
            {
                var handle = PrefabUtility.GetPrefabInstanceHandle(instance);
                var transform = instance.transform;
                if (handle != null)
                    placements[handle] = Matrix4x4.TRS(transform.localPosition, transform.localRotation, transform.localScale)
                                         * before.Value.inverse;
            }
            return placements;
        }

        static Matrix4x4? ModelRoot(string asset)
        {
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(asset);
            if (model == null)
                return null;
            var transform = model.transform;
            return Matrix4x4.TRS(transform.localPosition, transform.localRotation, transform.localScale);
        }

        // Presentation -------------------------------------------------------------------------------------------

        static void SetLinked(string asset)
        {
            Linked = asset;
            SessionState.SetString(LinkedKey, asset);
        }

        static void Notify(string text, bool warning)
        {
            if (string.IsNullOrEmpty(text))
                return;
            LastMessage = text;
            if (warning)
                Debug.LogWarning("Angelo: " + text);
            else
                Debug.Log("Angelo: " + text);
            var view = SceneView.lastActiveSceneView;
            if (view != null)
                view.ShowNotification(new GUIContent("Angelo: " + text), warning ? 4.0 : 2.0);
        }

        static void BringAngeloForward()
        {
            var app = Client.AppPath;   // Windows: Angelo flashes its own taskbar button.
            if (Application.platform == RuntimePlatform.OSXEditor && app.EndsWith(".app") && System.IO.Directory.Exists(app))
                System.Diagnostics.Process.Start("open", "-a \"" + app + "\"");
        }

        static string NewId()
        {
            _nextId++;
            return _nextId.ToString();
        }
    }

    /// <summary>Tells the link when Unity re-imported assets (Angelo's "saved", or any other import).</summary>
    sealed class AngeloLinkImportWatcher : AssetPostprocessor
    {
        static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
        {
            AngeloLink.OnImported(imported);
        }
    }
}
