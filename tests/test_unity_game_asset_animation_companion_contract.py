import unittest
from pathlib import Path


class UnityGameAssetAnimationCompanionContractTests(unittest.TestCase):
    def source(self) -> str:
        return (
            Path(__file__).parents[1]
            / "ordax_dev_agent"
            / "assets"
            / "OrdaXGameAssetAnimationAgent.cs"
        ).read_text(encoding="utf-8")

    def test_sampling_is_isolated_and_reversible(self) -> None:
        source = self.source()
        for expected in (
            'protocol = "ordax-game-assets-animation-v1"',
            'command.action != "asset_animation_sample_audit"',
            "AnimationMode.InAnimationMode()",
            "EditorSceneManager.NewPreviewScene()",
            "SceneManager.MoveGameObjectToScene(clone, previewScene)",
            "AnimationMode.StartAnimationMode()",
            "AnimationMode.BeginSampling()",
            "AnimationMode.SampleAnimationClip(root, clip, time)",
            "AnimationMode.EndSampling()",
            "AnimationMode.StopAnimationMode()",
            "EditorSceneManager.ClosePreviewScene(previewScene)",
            "UnityEngine.Object.DestroyImmediate(clone)",
        ):
            self.assertIn(expected, source)

    def test_sampling_measures_transform_and_blend_shape_pose_changes(self) -> None:
        source = self.source()
        for expected in (
            "SnapshotTransforms(transforms)",
            "SnapshotBlendShapes(skinnedRenderers)",
            "Vector3.Distance(",
            "Quaternion.Angle(",
            "renderer.GetBlendShapeWeight(shapeIndex)",
            "reply.changedTransformCount",
            "reply.changedBlendShapeCount",
            "reply.poseChanged = changedTransforms > 0 || changedBlendShapes > 0",
            "clip.hasRootCurves",
            "clip.hasMotionCurves",
            "clip.humanMotion",
            "clip.hasGenericRootTransform",
        ):
            self.assertIn(expected, source)

    def test_ambiguous_clip_selection_requires_an_explicit_name(self) -> None:
        source = self.source()
        self.assertIn("asset exposes multiple AnimationClips; clipName is required", source)
        self.assertIn("clipName must match exactly one AnimationClip", source)
        self.assertIn('path.StartsWith("Assets/", StringComparison.Ordinal)', source)


if __name__ == "__main__":
    unittest.main()
