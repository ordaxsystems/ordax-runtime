import unittest
from pathlib import Path


class UnityGameAssetCompanionContractTests(unittest.TestCase):
    def source(self) -> str:
        return (
            Path(__file__).parents[1]
            / "ordax_dev_agent"
            / "assets"
            / "OrdaXGameAssetAgent.cs"
        ).read_text(encoding="utf-8")

    def test_companion_reports_avatar_root_motion_and_lod_evidence(self) -> None:
        source = self.source()
        for expected in (
            'protocol = "ordax-game-assets-v1"',
            'case "asset_character_audit":',
            "importer.avatarSetup.ToString()",
            "Avatar sourceAvatar = importer.sourceAvatar",
            "sourceAvatarPresent",
            "avatar.GetInstanceID()",
            "avatar.isValid",
            "avatar.isHuman",
            "avatar.humanDescription.human",
            "clip.hasRootCurves",
            "clip.hasMotionCurves",
            "clip.humanMotion",
            "clip.hasGenericRootTransform",
            "GetComponentsInChildren<SkinnedMeshRenderer>(true)",
            "GetComponentsInChildren<LODGroup>(true)",
            ".GetLODs()",
            "lodEmptyLevelCount",
            "screenRelativeTransitionHeight",
            '"Library", "OrdaXAgent", "game-assets"',
        ):
            self.assertIn(expected, source)

    def test_companion_character_import_configuration_is_transactional(self) -> None:
        source = self.source()
        for expected in (
            'case "asset_character_import_configure":',
            "ModelImporterAnimationType.Human",
            "ModelImporterAvatarSetup.CreateFromThisModel",
            "ModelImporterAvatarSetup.CopyFromOther",
            "importer.sourceAvatar = desiredSourceAvatar",
            "importer.importAnimation = desiredImportAnimation",
            "importer.optimizeGameObjects = desiredOptimizeGameObjects",
            "importer.resampleCurves = desiredResampleCurves",
            "importer.isReadable = desiredIsReadable",
            "importer.SaveAndReimport()",
            "ImporterMatches(",
            "RestoreImporter(",
            "reply.rollbackAttempted = true",
            "reply.rollbackSucceeded = RestoreImporter(",
            "sourceAvatarPath must reference a different Unity asset",
        ):
            self.assertIn(expected, source)

    def test_companion_scopes_asset_requests_to_assets_directory(self) -> None:
        source = self.source()
        self.assertIn('path.StartsWith("Assets/", StringComparison.Ordinal)', source)
        self.assertIn('path.Contains("/../")', source)
        self.assertIn("AssetDatabase.LoadMainAssetAtPath(path)", source)


if __name__ == "__main__":
    unittest.main()
