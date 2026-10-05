import unittest
from pathlib import Path


class UnityModelImportAuditContractTests(unittest.TestCase):
    def test_generic_companion_forces_import_and_reports_runtime_model_evidence(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ordax_dev_agent"
            / "assets"
            / "OrdaXGenericAgent.cs"
        ).read_text(encoding="utf-8")

        for expected in (
            'case "asset_model_audit": AssetModelAudit(command, reply); break;',
            "AssetDatabase.ImportAsset(",
            "ImportAssetOptions.ForceSynchronousImport",
            "AssetImporter.GetAtPath(assetPath)",
            "AssetDatabase.LoadAssetAtPath<GameObject>(assetPath)",
            "modelImporterPresent",
            "modelGlobalScale",
            "modelReadable",
            "modelImportAnimation",
            "modelAnimationType",
            "modelMeshCompression",
            "modelMeshCount",
            "modelVertexCount",
            "modelTriangleCount",
            "modelMaterialCount",
            "modelAnimationClipCount",
            "modelBoneCount",
            "modelLodGroupCount",
            "GetComponentsInChildren<LODGroup>(true)",
            "GetComponentsInChildren<SkinnedMeshRenderer>(true)",
            "mesh.GetIndexCount(subMesh)",
        ):
            self.assertIn(expected, source)

        self.assertIn('!assetPath.StartsWith("Assets/", StringComparison.Ordinal)', source)
        self.assertIn("fullPath.StartsWith(rootPrefix, StringComparison.OrdinalIgnoreCase)", source)
        self.assertIn("meshes.Count > 0 && vertices > 0 && triangles > 0", source)


if __name__ == "__main__":
    unittest.main()
