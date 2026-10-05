#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace OrdaX.EditorTools
{
    [InitializeOnLoad]
    public static class OrdaXGameAssetAnimationAgent
    {
        [Serializable] private class Command
        {
            public string id, action, assetPath, clipName;
            public float normalizedSampleTime = 0.5f;
        }

        [Serializable] private class Reply
        {
            public string id, summary, protocol = "ordax-game-assets-animation-v1";
            public bool ok;
            public string assetPath, clipName;
            public string[] availableClipNames;
            public float clipLengthSeconds, normalizedSampleTime, sampleTimeSeconds;
            public bool humanMotion, hasRootCurves, hasMotionCurves, hasGenericRootTransform;
            public int transformCount, changedTransformCount, skinnedRendererCount;
            public int blendShapeChannelCount, changedBlendShapeCount;
            public float maxPositionDelta, maxRotationAngleDegrees, maxScaleDelta;
            public float maxBlendShapeWeightDelta, rootPositionDelta, rootRotationAngleDegrees;
            public bool poseChanged, previewSceneUsed, animationModeUsed;
        }

        private struct TransformState
        {
            public Vector3 position;
            public Quaternion rotation;
            public Vector3 scale;
        }

        private static string Root => Path.Combine(
            Directory.GetParent(Application.dataPath).FullName,
            "Library", "OrdaXAgent", "game-assets-animation");
        private static double nextPoll;

        static OrdaXGameAssetAnimationAgent()
        {
            EditorApplication.update += Tick;
        }

        private static Reply State(string id, bool ok, string summary)
        {
            return new Reply { id = id, ok = ok, summary = summary };
        }

        private static void Write(string path, object value)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string temporary = path + ".tmp";
            File.WriteAllText(temporary, JsonUtility.ToJson(value, true));
            if (File.Exists(path)) File.Replace(temporary, path, null);
            else File.Move(temporary, path);
        }

        private static void Tick()
        {
            if (EditorApplication.timeSinceStartup < nextPoll) return;
            nextPoll = EditorApplication.timeSinceStartup + 0.35;
            try
            {
                Write(Path.Combine(Root, "presence.json"), State("presence", true, "Animation sample companion ready"));
                if (EditorApplication.isCompiling || EditorApplication.isUpdating) return;
                string inbox = Path.Combine(Root, "inbox");
                Directory.CreateDirectory(inbox);
                var files = Directory.GetFiles(inbox, "*.json");
                Array.Sort(files, StringComparer.Ordinal);
                if (files.Length > 0) Process(files[0]);
            }
            catch (IOException) { }
        }

        private static void Process(string file)
        {
            string id = Path.GetFileNameWithoutExtension(file);
            if (!Guid.TryParseExact(id, "N", out _))
            {
                File.Delete(file);
                return;
            }

            Reply reply;
            try
            {
                var command = JsonUtility.FromJson<Command>(File.ReadAllText(file));
                File.Delete(file);
                if (command == null || command.id != id)
                    throw new InvalidOperationException("Command ID mismatch");
                if (command.action != "asset_animation_sample_audit")
                    throw new InvalidOperationException("Unsupported animation companion action: " + command.action);
                reply = State(id, true, "Unity animation sample completed");
                SampleAnimation(command, reply);
            }
            catch (Exception error)
            {
                reply = State(id, false, error.GetType().Name + ": " + error.Message);
            }
            Write(Path.Combine(Root, "responses", id + ".json"), reply);
        }

        private static string ValidateAssetPath(string value)
        {
            string path = (value ?? "").Replace('\\', '/').Trim();
            if (!path.StartsWith("Assets/", StringComparison.Ordinal) ||
                path.Contains("/../") || path.EndsWith("/..", StringComparison.Ordinal) ||
                path.Contains("/./"))
                throw new InvalidOperationException("assetPath must stay inside Assets/");
            if (AssetDatabase.LoadMainAssetAtPath(path) == null)
                throw new InvalidOperationException("assetPath is not a loaded Unity asset");
            return path;
        }

        private static AnimationClip SelectClip(AnimationClip[] clips, string requestedName)
        {
            if (clips.Length == 0)
                throw new InvalidOperationException("asset exposes no non-preview AnimationClip");
            if (string.IsNullOrWhiteSpace(requestedName))
            {
                if (clips.Length != 1)
                    throw new InvalidOperationException(
                        "asset exposes multiple AnimationClips; clipName is required: " +
                        string.Join(", ", clips.Select(clip => clip.name).ToArray()));
                return clips[0];
            }
            var matches = clips
                .Where(clip => string.Equals(clip.name, requestedName, StringComparison.Ordinal))
                .ToArray();
            if (matches.Length != 1)
                throw new InvalidOperationException(
                    "clipName must match exactly one AnimationClip; available: " +
                    string.Join(", ", clips.Select(clip => clip.name).ToArray()));
            return matches[0];
        }

        private static void Sample(GameObject root, AnimationClip clip, float time)
        {
            AnimationMode.BeginSampling();
            try
            {
                AnimationMode.SampleAnimationClip(root, clip, time);
            }
            finally
            {
                AnimationMode.EndSampling();
            }
        }

        private static TransformState[] SnapshotTransforms(Transform[] transforms)
        {
            var result = new TransformState[transforms.Length];
            for (int index = 0; index < transforms.Length; index++)
            {
                result[index] = new TransformState
                {
                    position = transforms[index].localPosition,
                    rotation = transforms[index].localRotation,
                    scale = transforms[index].localScale,
                };
            }
            return result;
        }

        private static Dictionary<string, float> SnapshotBlendShapes(SkinnedMeshRenderer[] renderers)
        {
            var result = new Dictionary<string, float>(StringComparer.Ordinal);
            for (int rendererIndex = 0; rendererIndex < renderers.Length; rendererIndex++)
            {
                var renderer = renderers[rendererIndex];
                var mesh = renderer.sharedMesh;
                if (mesh == null) continue;
                for (int shapeIndex = 0; shapeIndex < mesh.blendShapeCount; shapeIndex++)
                {
                    string key = rendererIndex + ":" + shapeIndex;
                    result[key] = renderer.GetBlendShapeWeight(shapeIndex);
                }
            }
            return result;
        }

        private static void SampleAnimation(Command command, Reply reply)
        {
            string assetPath = ValidateAssetPath(command.assetPath);
            if (command.normalizedSampleTime <= 0f || command.normalizedSampleTime > 1f)
                throw new InvalidOperationException("normalizedSampleTime must be > 0 and <= 1");
            if (AnimationMode.InAnimationMode())
                throw new InvalidOperationException(
                    "Unity Editor is already in AnimationMode; refusing to disturb the user's animation state");

            var clips = AssetDatabase.LoadAllAssetsAtPath(assetPath)
                .OfType<AnimationClip>()
                .Where(clip => clip != null && !clip.name.StartsWith("__preview__", StringComparison.Ordinal))
                .OrderBy(clip => clip.name, StringComparer.Ordinal)
                .ToArray();
            var clip = SelectClip(clips, command.clipName);
            if (clip.length <= 0f)
                throw new InvalidOperationException("selected AnimationClip has zero length");
            var modelRoot = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (modelRoot == null)
                throw new InvalidOperationException("Unity did not expose the imported model root GameObject");

            Scene previewScene = default;
            GameObject clone = null;
            bool animationModeStarted = false;
            try
            {
                previewScene = EditorSceneManager.NewPreviewScene();
                reply.previewSceneUsed = true;
                clone = UnityEngine.Object.Instantiate(modelRoot);
                clone.name = "OrdaXAnimationSample";
                clone.hideFlags = HideFlags.HideAndDontSave;
                SceneManager.MoveGameObjectToScene(clone, previewScene);

                var transforms = clone.GetComponentsInChildren<Transform>(true);
                var skinnedRenderers = clone.GetComponentsInChildren<SkinnedMeshRenderer>(true);

                AnimationMode.StartAnimationMode();
                animationModeStarted = true;
                reply.animationModeUsed = true;

                Sample(clone, clip, 0f);
                var baselineTransforms = SnapshotTransforms(transforms);
                var baselineBlendShapes = SnapshotBlendShapes(skinnedRenderers);
                Vector3 baselineRootPosition = clone.transform.localPosition;
                Quaternion baselineRootRotation = clone.transform.localRotation;

                float sampleTime = Mathf.Clamp(command.normalizedSampleTime, 0f, 1f) * clip.length;
                Sample(clone, clip, sampleTime);
                var sampledBlendShapes = SnapshotBlendShapes(skinnedRenderers);

                int changedTransforms = 0;
                float maxPosition = 0f;
                float maxRotation = 0f;
                float maxScale = 0f;
                for (int index = 0; index < transforms.Length; index++)
                {
                    float positionDelta = Vector3.Distance(
                        baselineTransforms[index].position, transforms[index].localPosition);
                    float rotationDelta = Quaternion.Angle(
                        baselineTransforms[index].rotation, transforms[index].localRotation);
                    float scaleDelta = Vector3.Distance(
                        baselineTransforms[index].scale, transforms[index].localScale);
                    maxPosition = Mathf.Max(maxPosition, positionDelta);
                    maxRotation = Mathf.Max(maxRotation, rotationDelta);
                    maxScale = Mathf.Max(maxScale, scaleDelta);
                    if (positionDelta > 0.00001f || rotationDelta > 0.01f || scaleDelta > 0.00001f)
                        changedTransforms++;
                }

                int changedBlendShapes = 0;
                float maxBlendShapeDelta = 0f;
                foreach (var pair in baselineBlendShapes)
                {
                    float sampled = sampledBlendShapes.TryGetValue(pair.Key, out var value) ? value : pair.Value;
                    float delta = Mathf.Abs(sampled - pair.Value);
                    maxBlendShapeDelta = Mathf.Max(maxBlendShapeDelta, delta);
                    if (delta > 0.001f) changedBlendShapes++;
                }

                reply.assetPath = assetPath;
                reply.clipName = clip.name;
                reply.availableClipNames = clips.Select(item => item.name).ToArray();
                reply.clipLengthSeconds = clip.length;
                reply.normalizedSampleTime = command.normalizedSampleTime;
                reply.sampleTimeSeconds = sampleTime;
                reply.humanMotion = clip.humanMotion;
                reply.hasRootCurves = clip.hasRootCurves;
                reply.hasMotionCurves = clip.hasMotionCurves;
                reply.hasGenericRootTransform = clip.hasGenericRootTransform;
                reply.transformCount = transforms.Length;
                reply.changedTransformCount = changedTransforms;
                reply.skinnedRendererCount = skinnedRenderers.Length;
                reply.blendShapeChannelCount = baselineBlendShapes.Count;
                reply.changedBlendShapeCount = changedBlendShapes;
                reply.maxPositionDelta = maxPosition;
                reply.maxRotationAngleDegrees = maxRotation;
                reply.maxScaleDelta = maxScale;
                reply.maxBlendShapeWeightDelta = maxBlendShapeDelta;
                reply.rootPositionDelta = Vector3.Distance(
                    baselineRootPosition, clone.transform.localPosition);
                reply.rootRotationAngleDegrees = Quaternion.Angle(
                    baselineRootRotation, clone.transform.localRotation);
                reply.poseChanged = changedTransforms > 0 || changedBlendShapes > 0;
            }
            finally
            {
                if (animationModeStarted && AnimationMode.InAnimationMode())
                    AnimationMode.StopAnimationMode();
                if (clone != null)
                    UnityEngine.Object.DestroyImmediate(clone);
                if (previewScene.IsValid())
                    EditorSceneManager.ClosePreviewScene(previewScene);
            }
        }
    }
}
#endif
