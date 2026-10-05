// OrdaX generated game-asset import contract for Unity 6+.
// Installed into Assets/Editor by the Device Agent. The adjacent
// <model>.ordax-unity.json sidecar is the authoritative import profile.

#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace OrdaX.Editor
{
    internal sealed class OrdaXGeneratedAssetProfile
    {
        public string schema;
        public string profile;
        public float globalScale = 1.0f;
        public bool importMaterials = true;
        public bool importAnimation = true;
        public bool importBlendShapes = true;
        public bool addCollider = false;
        public bool generateSecondaryUV = false;
    }

    /// <summary>
    /// Applies explicit OrdaX model-import sidecars before Unity builds the model
    /// asset. Updating a sidecar schedules exactly that model for ForceUpdate.
    /// </summary>
    internal sealed class OrdaXGeneratedAssetPostprocessor : AssetPostprocessor
    {
        internal const string SidecarSuffix = ".ordax-unity.json";
        internal const string ProfileSchema = "ordax.unity-model-profile/1";
        private static readonly HashSet<string> PendingReimports = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private static bool _delayScheduled;

        private static bool IsModelAsset(string assetPath)
        {
            string extension = Path.GetExtension(assetPath).ToLowerInvariant();
            return extension == ".fbx" || extension == ".obj";
        }

        private static string SidecarPath(string assetPath)
        {
            return assetPath + SidecarSuffix;
        }

        private static string AbsoluteProjectPath(string projectRelativePath)
        {
            return Path.GetFullPath(Path.Combine(Directory.GetCurrentDirectory(), projectRelativePath));
        }

        private static bool TryReadProfile(string assetPath, out OrdaXGeneratedAssetProfile profile)
        {
            profile = null;
            if (!assetPath.StartsWith("Assets/", StringComparison.Ordinal) || !IsModelAsset(assetPath))
                return false;

            string sidecar = AbsoluteProjectPath(SidecarPath(assetPath));
            if (!File.Exists(sidecar))
                return false;

            try
            {
                string json = File.ReadAllText(sidecar);
                profile = JsonUtility.FromJson<OrdaXGeneratedAssetProfile>(json);
                if (profile == null || !string.Equals(profile.schema, ProfileSchema, StringComparison.Ordinal))
                {
                    Debug.LogError($"OrdaX: invalid Unity model profile schema for {assetPath}");
                    profile = null;
                    return false;
                }
                return true;
            }
            catch (Exception exception)
            {
                Debug.LogError($"OrdaX: failed to read model profile for {assetPath}: {exception.Message}");
                profile = null;
                return false;
            }
        }

        private void OnPreprocessModel()
        {
            if (!(assetImporter is ModelImporter importer))
                return;
            if (!TryReadProfile(assetPath, out OrdaXGeneratedAssetProfile profile))
                return;

            importer.globalScale = Mathf.Max(0.0001f, profile.globalScale);
            importer.importCameras = false;
            importer.importLights = false;
            importer.importBlendShapes = profile.importBlendShapes;
            importer.addCollider = profile.addCollider;
            importer.generateSecondaryUV = profile.generateSecondaryUV;
            importer.importAnimation = profile.importAnimation;
            importer.materialImportMode = profile.importMaterials
                ? ModelImporterMaterialImportMode.ImportStandard
                : ModelImporterMaterialImportMode.None;

            switch ((profile.profile ?? string.Empty).Trim().ToLowerInvariant())
            {
                case "static":
                    importer.animationType = ModelImporterAnimationType.None;
                    importer.avatarSetup = ModelImporterAvatarSetup.NoAvatar;
                    importer.importAnimation = false;
                    break;
                case "humanoid":
                    importer.animationType = ModelImporterAnimationType.Human;
                    importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                    importer.autoGenerateAvatarMappingIfUnspecified = true;
                    importer.importAnimation = true;
                    break;
                case "generic":
                    importer.animationType = ModelImporterAnimationType.Generic;
                    importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                    importer.importAnimation = true;
                    break;
                default:
                    Debug.LogError($"OrdaX: unsupported model profile '{profile.profile}' for {assetPath}");
                    break;
            }
        }

        private static void QueueReimport(string modelPath)
        {
            if (!modelPath.StartsWith("Assets/", StringComparison.Ordinal) || !IsModelAsset(modelPath))
                return;
            PendingReimports.Add(modelPath);
            if (_delayScheduled)
                return;
            _delayScheduled = true;
            EditorApplication.delayCall += FlushReimports;
        }

        private static void FlushReimports()
        {
            _delayScheduled = false;
            string[] pending = new string[PendingReimports.Count];
            PendingReimports.CopyTo(pending);
            PendingReimports.Clear();
            foreach (string modelPath in pending)
            {
                if (File.Exists(AbsoluteProjectPath(modelPath)))
                    AssetDatabase.ImportAsset(modelPath, ImportAssetOptions.ForceUpdate);
            }
        }

        private static void OnPostprocessAllAssets(
            string[] importedAssets,
            string[] deletedAssets,
            string[] movedAssets,
            string[] movedFromAssetPaths)
        {
            foreach (string imported in importedAssets)
            {
                if (!imported.EndsWith(SidecarSuffix, StringComparison.OrdinalIgnoreCase))
                    continue;
                string modelPath = imported.Substring(0, imported.Length - SidecarSuffix.Length);
                QueueReimport(modelPath);
            }

            for (int index = 0; index < movedAssets.Length; index++)
            {
                string moved = movedAssets[index];
                if (!moved.EndsWith(SidecarSuffix, StringComparison.OrdinalIgnoreCase))
                    continue;
                string modelPath = moved.Substring(0, moved.Length - SidecarSuffix.Length);
                QueueReimport(modelPath);
            }
        }
    }
}
#endif
