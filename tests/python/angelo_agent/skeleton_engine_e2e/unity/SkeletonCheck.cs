// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// The Unity side of skeleton_engine_e2e/run.py (-batchmode -executeMethod AngeloSkeletonTests.SkeletonCheck.Run).
// Imports every FBX in Assets/Rigs as a Humanoid (Unity's own automap builds the avatar), records whether the avatar
// is valid and human, the bones Unity mapped to each slot, and Unity's own required slots (HumanTrait).
// Writes ANGELO_UNITY_REPORT and exits. Test only; never shipped.
using System.Collections.Generic;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace AngeloSkeletonTests
{
    public static class SkeletonCheck
    {
        static string Quote(string s) { return "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"") + "\""; }

        public static void Run()
        {
            var json = new StringBuilder();
            json.Append("{\"unity\":").Append(Quote(Application.unityVersion)).Append(",\"required\":[");
            var first = true;
            for (int i = 0; i < HumanTrait.BoneCount; i++)
            {
                if (!HumanTrait.RequiredBone(i)) continue;
                json.Append(first ? "" : ",").Append(Quote(HumanTrait.BoneName[i]));
                first = false;
            }
            json.Append("],\"rigs\":{");
            first = true;
            foreach (var path in Directory.GetFiles("Assets/Rigs", "*.fbx"))
            {
                var importer = (ModelImporter)AssetImporter.GetAtPath(path);
                importer.animationType = ModelImporterAnimationType.Human;
                importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                importer.SaveAndReimport();
                Avatar avatar = null;
                foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(path))
                    if (asset is Avatar found) avatar = found;
                json.Append(first ? "" : ",").Append(Quote(Path.GetFileName(path))).Append(":{");
                first = false;
                json.Append("\"avatar\":").Append(avatar != null ? "true" : "false");
                json.Append(",\"valid\":").Append(avatar != null && avatar.isValid ? "true" : "false");
                json.Append(",\"human\":").Append(avatar != null && avatar.isHuman ? "true" : "false");
                json.Append(",\"mapped\":{");
                var firstBone = true;
                foreach (var bone in importer.humanDescription.human)
                {
                    json.Append(firstBone ? "" : ",").Append(Quote(bone.humanName)).Append(":").Append(Quote(bone.boneName));
                    firstBone = false;
                }
                json.Append("}}");
            }
            json.Append("}}");
            File.WriteAllText(System.Environment.GetEnvironmentVariable("ANGELO_UNITY_REPORT"), json.ToString());
            EditorApplication.Exit(0);
        }
    }
}
