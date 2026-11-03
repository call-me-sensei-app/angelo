// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// What glTFast's editor importer made of Angelo's link.glb in this project's render pipeline: each renderer's
// materials, their shader and the maps they use, as JSON at $ANGELO_CHECK_OUT, and a lit render at $ANGELO_CHECK_PNG.
// run.py copies it into a throwaway project's Assets/Editor and calls AngeloBakedCheck.Run in batch mode.
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;

public static class AngeloBakedCheck
{
    static readonly string[] Maps = { "baseColorTexture", "metallicRoughnessTexture", "occlusionTexture",
                                      "normalTexture", "emissiveTexture" };

    static string Quote(string text) => "\"" + (text ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";

    public static void Run()
    {
        var json = new StringBuilder();
        try
        {
            AssetDatabase.ImportAsset("Assets/link.glb", ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            var model = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/link.glb");
            var pipeline = GraphicsSettings.currentRenderPipeline;
            json.Append("{\"unity\": ").Append(Quote(Application.unityVersion));
            json.Append(", \"pipeline\": ").Append(Quote(pipeline == null ? "Built-in" : pipeline.GetType().Name));
            json.Append(", \"imported\": ").Append(model != null ? "true" : "false").Append(", \"objects\": {");
            var first = true;
            foreach (var renderer in model != null ? model.GetComponentsInChildren<Renderer>() : new Renderer[0])
            {
                json.Append(first ? "" : ", ").Append(Quote(renderer.gameObject.name)).Append(": [");
                first = false;
                var firstMaterial = true;
                foreach (var material in renderer.sharedMaterials)
                {
                    json.Append(firstMaterial ? "" : ", ");
                    firstMaterial = false;
                    if (material == null) { json.Append("null"); continue; }
                    json.Append("{\"name\": ").Append(Quote(material.name)).Append(", \"shader\": ").Append(Quote(material.shader.name));
                    foreach (var map in Maps)
                        json.Append(", ").Append(Quote(map)).Append(": ").Append(
                            material.HasProperty(map) && material.GetTexture(map) != null ? "true" : "false");
                    var shared = material.HasProperty("occlusionTexture") && material.HasProperty("metallicRoughnessTexture")
                        && material.GetTexture("occlusionTexture") != null
                        && material.GetTexture("occlusionTexture") == material.GetTexture("metallicRoughnessTexture");
                    json.Append(", \"orm_shared\": ").Append(shared ? "true" : "false").Append("}");
                }
                json.Append("]");
            }
            json.Append("}");
            var png = System.Environment.GetEnvironmentVariable("ANGELO_CHECK_PNG");
            if (model != null && !string.IsNullOrEmpty(png))
                json.Append(", \"rendered\": ").Append(Render(model, png) ? "true" : "false");
            json.Append("}");
            File.WriteAllText(System.Environment.GetEnvironmentVariable("ANGELO_CHECK_OUT"), json.ToString());
            EditorApplication.Exit(0);
        }
        catch (System.Exception error)
        {
            File.WriteAllText(System.Environment.GetEnvironmentVariable("ANGELO_CHECK_OUT"),
                              "{\"error\": " + Quote(error.ToString()) + "}");
            EditorApplication.Exit(1);
        }
    }

    /// <summary>A throwaway project only: make URP its render pipeline (a URP asset with its default renderer), so
    /// glTFast's next import builds URP materials. Reflection, so this file also compiles where URP isn't installed.</summary>
    public static void UseUrp()
    {
        var type = System.Type.GetType("UnityEngine.Rendering.Universal.UniversalRenderPipelineAsset, Unity.RenderPipelines.Universal.Runtime");
        var create = type?.GetMethod("Create", new[] { System.Type.GetType("UnityEngine.Rendering.Universal.ScriptableRendererData, Unity.RenderPipelines.Universal.Runtime") });
        var asset = create?.Invoke(null, new object[] { null }) as RenderPipelineAsset;
        if (asset == null) { EditorApplication.Exit(2); return; }
        AssetDatabase.CreateAsset(asset, "Assets/AngeloCheckURP.asset");
        GraphicsSettings.defaultRenderPipeline = asset;
        QualitySettings.renderPipeline = asset;
        AssetDatabase.SaveAssets();
        EditorApplication.Exit(0);
    }

    static bool Render(GameObject model, string path)
    {
        var instance = (GameObject)Object.Instantiate(model);
        var sun = new GameObject("Sun").AddComponent<Light>();
        sun.type = LightType.Directional;
        sun.transform.rotation = Quaternion.Euler(50f, -30f, 0f);
        RenderSettings.ambientLight = new Color(0.35f, 0.35f, 0.4f);
        var camera = new GameObject("Camera").AddComponent<Camera>();
        camera.transform.position = new Vector3(-2.4f, 2.2f, 4.2f);   // glTF +X is Unity -X (glTFast flips X).
        camera.transform.LookAt(new Vector3(-2.4f, 0.2f, 0f));
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(0.12f, 0.12f, 0.14f);
        var target = new RenderTexture(960, 540, 24);
        camera.targetTexture = target;
        if (GraphicsSettings.currentRenderPipeline == null)
            camera.Render();
        else   // A scriptable pipeline (URP) ignores Camera.Render; a render request draws into the texture.
        {
            var request = new RenderPipeline.StandardRequest { destination = target };
            if (!RenderPipeline.SupportsRenderRequest(camera, request))
                return false;
            RenderPipeline.SubmitRenderRequest(camera, request);
        }
        RenderTexture.active = target;
        var picture = new Texture2D(960, 540, TextureFormat.RGB24, false);
        picture.ReadPixels(new Rect(0, 0, 960, 540), 0, 0);
        picture.Apply();
        File.WriteAllBytes(path, picture.EncodeToPNG());
        RenderTexture.active = null;
        Object.DestroyImmediate(instance);
        return true;
    }
}
