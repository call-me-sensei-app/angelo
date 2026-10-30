// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The entry points: "Edit in Angelo" in the Project window's context menu (Assets/) on a .blend, .glb or .fbx, in the
// Hierarchy's context menu (GameObject/, priority 10 so Unity shows it there) on anything made from one, Tools >
// Angelo Link, and the Scene view's Angelo Link overlay (a toolbar dropdown).

using System.IO;
using UnityEditor;
using UnityEditor.Overlays;
using UnityEditor.Toolbars;
using UnityEngine;

namespace Hyperbond.AngeloLink
{
    static class EditMenus
    {
        const string ProjectItem = "Assets/Edit in Angelo";
        const string HierarchyItem = "GameObject/Edit in Angelo";
        const string StopItem = "Tools/Angelo Link/Stop Live Link";
        const string EditItem = "Tools/Angelo Link/Edit Selection in Angelo";

        [MenuItem(ProjectItem, false, 1100)]
        static void EditProjectAsset() => AngeloLink.EditAsset(SelectedAssetPath(), "");

        [MenuItem(ProjectItem, true)]
        static bool CanEditProjectAsset() => AssetRules.IsEditable(SelectedAssetPath());

        [MenuItem(HierarchyItem, false, 10)]
        static void EditHierarchyObject() => AngeloLink.EditGameObject(Selection.activeGameObject);

        [MenuItem(HierarchyItem, true)]
        static bool CanEditHierarchyObject() => SelectedTarget() != null;

        [MenuItem(EditItem, false, 1)]
        static void EditSelection() => EditSelected();

        [MenuItem(EditItem, true)]
        static bool CanEditSelection() => SelectedTarget() != null || AssetRules.IsEditable(SelectedAssetPath());

        [MenuItem(StopItem, false, 2)]
        static void Stop() => AngeloLink.StopLink();

        [MenuItem(StopItem, true)]
        static bool CanStop() => AngeloLink.Linked != "";

        /// <summary>The selected scene object's asset or the selected model file, whichever there is.</summary>
        internal static void EditSelected()
        {
            var gameObject = Selection.activeGameObject;
            if (gameObject != null && gameObject.scene.IsValid())
                AngeloLink.EditGameObject(gameObject);
            else if (AssetRules.IsEditable(SelectedAssetPath()))
                AngeloLink.EditAsset(SelectedAssetPath(), "");
        }

        /// <summary>The asset of the selected scene object (null when it has none or nothing is selected).</summary>
        internal static string SelectedTarget()
        {
            var gameObject = Selection.activeGameObject;
            return gameObject != null && gameObject.scene.IsValid() ? AssetRules.AssetOf(gameObject) : null;
        }

        static string SelectedAssetPath()
        {
            var guids = Selection.assetGUIDs;
            return guids != null && guids.Length == 1 ? AssetDatabase.GUIDToAssetPath(guids[0]) : "";
        }
    }

    /// <summary>The Scene view's Angelo Link overlay: one dropdown with Edit, Stop and the link's state.</summary>
    [Overlay(typeof(SceneView), OverlayId, "Angelo Link")]
    sealed class AngeloLinkOverlay : ToolbarOverlay
    {
        public const string OverlayId = "angelo-link";

        public AngeloLinkOverlay() : base(AngeloLinkDropdown.Id) { }
    }

    [EditorToolbarElement(Id, typeof(SceneView))]
    sealed class AngeloLinkDropdown : EditorToolbarDropdown
    {
        public const string Id = "AngeloLink/Dropdown";

        public AngeloLinkDropdown()
        {
            text = "Angelo";
            tooltip = "Edit models in Angelo and see the changes here live";
            clicked += ShowMenu;
            schedule.Execute(() => text = AngeloLink.Linked != "" ? "Angelo · Live" : "Angelo").Every(500);
        }

        void ShowMenu()
        {
            var menu = new GenericMenu();
            var target = EditMenus.SelectedTarget();
            var label = target != null ? "Edit " + Path.GetFileName(target) + " in Angelo" : "Edit Selection in Angelo";
            if (target != null)
                menu.AddItem(new GUIContent(label), false, EditMenus.EditSelected);
            else
                menu.AddDisabledItem(new GUIContent(label));
            if (AngeloLink.Linked != "")
                menu.AddItem(new GUIContent("Stop Live Link"), false, AngeloLink.StopLink);
            else
                menu.AddDisabledItem(new GUIContent("Stop Live Link"));
            menu.AddSeparator("");
            menu.AddDisabledItem(new GUIContent(AngeloLink.StatusText()));
            if (LivePreview.Builder == null)
                menu.AddDisabledItem(new GUIContent("Live preview needs glTFast (com.unity.cloud.gltfast)"));
            menu.DropDown(worldBound);
        }
    }
}
