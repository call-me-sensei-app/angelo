// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
//
// The Unity side of unity_link_e2e/editor_run.py, run in a real Unity Editor in batchmode (-executeMethod
// AngeloLinkTests.BatchTest.Run, no -quit: this class exits the editor itself). It drives the shipped package
// the way a person does: a scene with two instances of the model, Edit in Angelo, Angelo's edit and save, the
// package's re-import, Stop Live Link; and writes what it saw to ANGELO_UNITY_REPORT. Test only; never shipped.

using System.Collections.Generic;
using System.IO;
using System.Linq;
using Hyperbond.AngeloLink;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace AngeloLinkTests   // Outside Hyperbond.*: there "AngeloLink" would name the package's namespace, not its class.
{
    public static class BatchTest
    {
        const string ScenePath = "Assets/LinkTest.unity";
        static readonly Dictionary<string, object> Report = new Dictionary<string, object>();
        static readonly Dictionary<string, object> Checks = new Dictionary<string, object>();
        static readonly List<string> Steps = new List<string>();
        static string _asset, _phase = "start", _sceneHash = "";
        static double _started, _phaseAt, _limit;
        static float _maxBefore;
        static Quaternion _rootBefore;
        static bool _sawScene;

        public static void Run()
        {
            _asset = System.Environment.GetEnvironmentVariable("ANGELO_UNITY_ASSET") ?? "Assets/crate.fbx";
            _limit = double.Parse(System.Environment.GetEnvironmentVariable("ANGELO_UNITY_SECONDS") ?? "150");
            _started = EditorApplication.timeSinceStartup;
            Report["unity"] = Application.unityVersion;
            Report["asset"] = _asset;
            Report["batchmode"] = Application.isBatchMode;
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(_asset);
            Checks["model_imported"] = prefab != null;
            if (prefab == null)
            {
                Finish("Unity didn't import " + _asset);
                return;
            }
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var first = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
            var second = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
            second.transform.position = new Vector3(4, 0, 0);
            EditorSceneManager.SaveScene(scene, ScenePath);
            _sceneHash = Hash(ScenePath);
            _maxBefore = MaxY(first);
            _rootBefore = prefab.transform.localRotation;
            Report["max_y_before"] = _maxBefore;
            Checks["asset_of_instance"] = AssetRules.AssetOf(first) == _asset;
            Checks["two_instances_found"] = AssetRules.FindInstances(_asset).Count == 2;
            AngeloLink.EditAsset(_asset, first.name);
            Step("asked");
            EditorApplication.update += Tick;
        }

        static void Tick()
        {
            var now = EditorApplication.timeSinceStartup;
            if (now - _started > _limit)
            {
                Finish("timed out in phase " + _phase + " (" + AngeloLink.LastMessage + ")");
                return;
            }
            if (AngeloLink.Preview.HasExport)
                _sawScene = true;
            switch (_phase)
            {
                case "asked":
                    if (AngeloLink.Linked == _asset)
                    {
                        Checks["linked"] = true;
                        Step("linked");
                    }
                    break;
                case "linked":
                    var instances = AssetRules.FindInstances(_asset);
                    if (instances.Count == 2 && Grew(instances[0]))
                    {   // The re-import of Angelo's save: every instance shows the written model (Angelo adds a cube
                        // above the crate, so the top rises by more than half the crate's height at any import scale).
                        Checks["reimport_holds_edit"] = instances.All(Grew);
                        Checks["reimport_holds_every_mesh"] = instances.All(MatchesExport);
                        Report["max_y_after"] = MaxY(instances[0]);
                        Checks["live_scene_received"] = _sawScene;
                        Checks["preview_says_it_needs_gltfast"] = LivePreview.Builder != null
                            || AngeloLink.Preview.LastError.Contains("glTFast");
                        Checks["originals_render_after_reimport"] = instances.All(Renders);
                        Checks["no_preview_objects_in_scene"] = !EditorSceneManager.GetActiveScene().GetRootGameObjects()
                            .Any(item => item.name == LivePreview.PreviewName);
                        // A model whose root changed (one object became two) keeps its instances in place by
                        // changing their root transforms: the scene then waits for the person's save. Otherwise
                        // nothing in the scene changes.
                        var model = AssetDatabase.LoadAssetAtPath<GameObject>(_asset);
                        var rootChanged = Quaternion.Angle(model.transform.localRotation, _rootBefore) > 0.01f;
                        Report["model_root_changed"] = rootChanged;
                        Checks[rootChanged ? "placement_kept_awaits_save" : "scene_not_dirty"] =
                            EditorSceneManager.GetActiveScene().isDirty == rootChanged;
                        Checks["scene_file_unchanged"] = Hash(ScenePath) == _sceneHash;
                        AngeloLink.StopLink();   // The person's Stop Live Link: Angelo hears close and ends.
                        Step("stopped");
                    }
                    break;
                case "stopped":
                    if (now - _phaseAt > 2.0)
                    {   // Let the close leave before the editor quits.
                        Checks["unlinked"] = AngeloLink.Linked == "";
                        Checks["originals_render_after_stop"] = AssetRules.FindInstances(_asset).All(Renders);
                        Finish(null);
                    }
                    break;
            }
        }

        static void Step(string phase)
        {
            _phase = phase;
            _phaseAt = EditorApplication.timeSinceStartup;
            Steps.Add(phase + " @" + (_phaseAt - _started).ToString("0.0"));
        }

        /// <summary>Whether an instance shows the whole scene Angelo saved (the Angelo side's export.txt): every exported
        /// object, the same triangles, the same height range (Unity's y is Blender's z) and the same two horizontal
        /// extents in either order, relative to the instance's own place. A re-import that kept only some meshes fails.
        /// </summary>
        static bool MatchesExport(GameObject root)
        {
            var path = System.Environment.GetEnvironmentVariable("ANGELO_UNITY_EXPORT");
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
                return false;
            var lines = File.ReadAllLines(path);
            var numbers = lines[0].Split(' ').Select(item => float.Parse(item, System.Globalization.CultureInfo.InvariantCulture)).ToArray();
            var triangles = int.Parse(lines[1]);
            var names = lines[2].Split(',');
            var filters = root.GetComponentsInChildren<MeshFilter>().Where(item => item.sharedMesh != null).ToList();
            var shown = filters.Select(item => item.name).ToList();
            var points = filters.SelectMany(item => Corners(item.sharedMesh.bounds).Select(corner =>
                root.transform.InverseTransformPoint(item.transform.TransformPoint(corner)))).ToList();
            if (points.Count == 0)
                return false;
            var low = new Vector3(points.Min(p => p.x), points.Min(p => p.y), points.Min(p => p.z));
            var high = new Vector3(points.Max(p => p.x), points.Max(p => p.y), points.Max(p => p.z));
            System.Func<float, float, bool> close = (a, b) => Mathf.Abs(a - b) <= 1e-3f;
            var across = new[] { high.x - low.x, high.z - low.z }.OrderBy(v => v).ToArray();
            var expected = new[] { numbers[3] - numbers[0], numbers[4] - numbers[1] }.OrderBy(v => v).ToArray();
            Report["instance_bounds"] = low.ToString("0.###") + " " + high.ToString("0.###");
            return names.All(shown.Contains) && filters.Sum(item => item.sharedMesh.triangles.Length / 3) == triangles
                && close(low.y, numbers[2]) && close(high.y, numbers[5])
                && close(across[0], expected[0]) && close(across[1], expected[1]);
        }

        static bool Grew(GameObject root)
        {
            return MaxY(root) > _maxBefore + 0.5f * Mathf.Abs(_maxBefore);
        }

        static float MaxY(GameObject root)
        {   // From each mesh and its transform: Renderer.bounds can lag in -nographics batchmode.
            var tops = root.GetComponentsInChildren<MeshFilter>().Where(item => item.sharedMesh != null)
                .SelectMany(item => Corners(item.sharedMesh.bounds).Select(corner => item.transform.TransformPoint(corner).y))
                .ToList();
            return tops.Count == 0 ? float.NaN : tops.Max();
        }

        static IEnumerable<Vector3> Corners(Bounds box)
        {
            for (var i = 0; i < 8; i++)
                yield return new Vector3((i & 1) == 0 ? box.min.x : box.max.x, (i & 2) == 0 ? box.min.y : box.max.y,
                                         (i & 4) == 0 ? box.min.z : box.max.z);
        }

        static string Describe(List<GameObject> roots)
        {   // Each root's renderers: name, bounds' top, whether it is switched off (a timed-out run says why).
            return string.Join(" | ", roots.Select(root => string.Join(", ", root.GetComponentsInChildren<Renderer>(true)
                .Select(item => item.name + " top=" + item.bounds.max.y.ToString("0.###") + " y="
                    + item.transform.position.y.ToString("0.###") + (item.forceRenderingOff ? " off" : "")))));
        }

        static bool Renders(GameObject root)
        {
            return root.GetComponentsInChildren<Renderer>().All(item => !item.forceRenderingOff);
        }

        static string Hash(string path)
        {
            using (var sha = System.Security.Cryptography.SHA256.Create())
                return System.BitConverter.ToString(sha.ComputeHash(File.ReadAllBytes(path)));
        }

        static void Finish(string problem)
        {
            EditorApplication.update -= Tick;
            Report["instances_at_end"] = Describe(AssetRules.FindInstances(_asset));
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(_asset);
            Report["model_at_end"] = prefab == null ? "missing" : Describe(new List<GameObject> { prefab });
            Report["steps"] = Steps;
            Report["checks"] = Checks;
            Report["last_message"] = AngeloLink.LastMessage;
            Report["problem"] = problem ?? "";
            var ok = problem == null && Checks.Count > 0 && Checks.Values.All(value => value is bool flag && flag);
            Report["ok"] = ok;
            File.WriteAllText(System.Environment.GetEnvironmentVariable("ANGELO_UNITY_REPORT"), Json(Report));
            EditorApplication.Exit(ok ? 0 : 1);
        }

        static string Json(object value)
        {
            switch (value)
            {
                case null: return "null";
                case bool flag: return flag ? "true" : "false";
                case string text: return "\"" + text.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n") + "\"";
                case float number: return float.IsNaN(number) ? "null" : number.ToString(System.Globalization.CultureInfo.InvariantCulture);
                case Dictionary<string, object> map:
                    return "{" + string.Join(",", map.Select(item => Json(item.Key) + ":" + Json(item.Value))) + "}";
                case List<string> list:
                    return "[" + string.Join(",", list.Select(Json)) + "]";
                default: return Json(value.ToString());
            }
        }
    }
}
