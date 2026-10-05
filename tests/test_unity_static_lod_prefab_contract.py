import unittest
from pathlib import Path


class UnityStaticLodPrefabContractTests(unittest.TestCase):
    def test_generic_companion_builds_only_guarded_static_lod_prefabs(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "ordax_dev_agent"
            / "assets"
            / "OrdaXGenericAgent.cs"
        ).read_text(encoding="utf-8")

        for expected in (
            'case "asset_lod_prefab_build": AssetLodPrefabBuild(command, reply); break;',
            "modelBlendShapeCount",
            "mesh.blendShapeCount",
            "Static LOD prefab generation refuses skinned models",
            "Static LOD prefab generation refuses blend shapes",
            "Every renderer must belong to a _LOD<n> object/hierarchy",
            "LOD levels must be contiguous from _LOD0",
            "lodThresholds count must match detected LOD level count",
            "LOD transition heights must be strictly descending",
            "Prefab already exists; set overwrite=true explicitly",
            "PrefabUtility.InstantiatePrefab(modelRoot)",
            "instance.AddComponent<LODGroup>()",
            "lodGroup.SetLODs(lods)",
            "lodGroup.RecalculateBounds()",
            "PrefabUtility.SaveAsPrefabAsset(instance, prefabPath)",
            "lodLevelCount",
            "lodRendererCount",
            "lodTransitionHeights",
        ):
            self.assertIn(expected, source)

        self.assertIn('new[] { ".prefab" }', source)
        self.assertIn("AssetDatabase.LoadMainAssetAtPath(prefabPath)", source)
        self.assertIn("UnityEngine.Object.DestroyImmediate(instance)", source)


if __name__ == "__main__":
    unittest.main()
