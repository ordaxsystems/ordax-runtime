#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

// Portable companion: no game-specific types, scene names or assets.
namespace OrdaX.EditorTools
{
    [InitializeOnLoad]
    public static class OrdaXGenericAgent
    {
        [Serializable] private class Command
        {
            public string id, action, outputPath, scenePath, assetPath, prefabPath;
            public int width = 1280, height = 720;
            public bool overwrite;
            public float[] lodThresholds;
        }
        [Serializable] private class Reply
        {
            public string id, summary, artifact, snapshotPath;
            public bool ok, compiling, playing;
            public string unityVersion = Application.unityVersion;
            public string protocol = "ordax-generic-v5";
            public int errorCount, warningCount;
            public string activeScene, renderPipeline;
            public int gameObjectCount, activeGameObjectCount;
            public int rigidbodyCount, colliderCount, meshColliderCount;
            public int rendererCount, cameraCount, lightCount, canvasCount;
            public int rigidbodyWithoutColliderCount, dynamicNonConvexMeshColliderCount;
            public int mirroredTransformCount, nearZeroScaleCount, extremeScaleCount;
            public int extremePositionCount, nonFiniteTransformCount, invertedRootCount;
            public int cameraInsideColliderCount, cameraBoundsContainingColliderCount;
            public bool cameraBelowRenderBounds;
            public float cameraGroundDistance = -1f;
            public Vector3 renderBoundsCenter, renderBoundsSize;
            public Vector3 cameraPosition, cameraEulerAngles, cameraGroundPoint;
            public string cameraGroundCollider;
            public string[] cameraInsideColliderNames, cameraBoundsContainingColliderNames;
            public string[] auditWarnings;
            public string assetPath, assetImporterType, modelAnimationType, modelMeshCompression;
            public bool modelImporterPresent, modelReadable, modelImportAnimation;
            public float modelGlobalScale;
            public int modelMeshCount, modelVertexCount, modelTriangleCount, modelMaterialCount;
            public int modelAnimationClipCount, modelBoneCount, modelBlendShapeCount, modelLodGroupCount;
            public string prefabPath;
            public int lodLevelCount, lodRendererCount;
            public float[] lodTransitionHeights;
        }
        [Serializable] private class SceneObject
        {
            public string name, path;
            public bool active;
            public Vector3 position, rotation, scale;
        }
        [Serializable] private class Snapshot
        {
            public string capturedAtUtc, scene, camera;
            public bool playing, objectsTruncated;
            public int objectCount;
            public SceneObject[] objects;
        }
        private static string Root => Path.Combine(Directory.GetParent(Application.dataPath).FullName, "Library", "OrdaXAgent");
        private static double nextPoll;
        private static int errors, warnings;
        static OrdaXGenericAgent()
        {
            EditorApplication.update += Tick;
            Application.logMessageReceived += OnLog;
        }
        private static void OnLog(string message, string stack, LogType type)
        {
            if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert) errors++;
            if (type == LogType.Warning) warnings++;
        }
        private static Reply State(string id, bool ok, string summary)
        {
            return new Reply { id = id, ok = ok, summary = summary,
                compiling = EditorApplication.isCompiling, playing = EditorApplication.isPlaying,
                errorCount = errors, warningCount = warnings };
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
                Write(Path.Combine(Root, "editor-presence.json"), State("presence", true, "Generic Unity companion ready"));
                if (EditorApplication.isCompiling || EditorApplication.isUpdating) return;
                string inbox = Path.Combine(Root, "inbox");
                Directory.CreateDirectory(inbox);
                var files = Directory.GetFiles(inbox, "*.json");
                Array.Sort(files, StringComparer.Ordinal);
                if (files.Length > 0) Process(files[0]);
            }
            catch (IOException) { /* Retry atomic file access next tick. */ }
        }
        private static void Process(string file)
        {
            Command command = null;
            string id = Path.GetFileNameWithoutExtension(file);
            if (!Guid.TryParseExact(id, "N", out _)) { File.Delete(file); return; }
            Reply reply;
            try
            {
                command = JsonUtility.FromJson<Command>(File.ReadAllText(file));
                File.Delete(file);
                if (command == null || command.id != id) throw new InvalidOperationException("Command ID mismatch");
                reply = State(id, true, "Completed");
                switch (command.action)
                {
                    case "health": reply.summary = "Generic companion ready"; break;
                    case "refresh":
                        // A refresh may trigger a domain reload. Persist the ACK first so
                        // the controller can wait for the next fresh presence heartbeat.
                        reply.summary = "Asset refresh requested";
                        Write(Path.Combine(Root, "responses", id + ".json"), reply);
                        AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                        return;
                    case "validate":
                        reply.ok = errors == 0;
                        reply.summary = "Editor ready; errors observed since companion reload: " + errors;
                        break;
                    case "play_start":
                    case "play_stop":
                        // Persist acknowledgment before a Play Mode domain reload.
                        reply.summary = "Play Mode transition requested";
                        Write(Path.Combine(Root, "responses", id + ".json"), reply);
                        EditorApplication.isPlaying = command.action == "play_start";
                        return;
                    case "capture": Capture(command, reply); break;
                    case "scene_open": OpenScene(command, reply); break;
                    case "scene_summary": SceneSummary(reply, false); break;
                    case "physics_audit": SceneSummary(reply, true); break;
                    case "spatial_audit": SpatialAudit(reply); break;
                    case "asset_model_audit": AssetModelAudit(command, reply); break;
                    case "asset_lod_prefab_build": AssetLodPrefabBuild(command, reply); break;
                    default: throw new InvalidOperationException("Unsupported companion action: " + command.action);
                }
            }
            catch (Exception error) { reply = State(id, false, error.GetType().Name + ": " + error.Message); }
            Write(Path.Combine(Root, "responses", id + ".json"), reply);
        }
        private static void SceneSummary(Reply reply, bool physicsAudit)
        {
            var sceneObjects = Resources.FindObjectsOfTypeAll<GameObject>()
                .Where(go => go != null && go.scene.IsValid() && go.scene.isLoaded)
                .ToArray();
            var rigidbodies = Resources.FindObjectsOfTypeAll<Rigidbody>()
                .Where(rb => rb != null && rb.gameObject.scene.IsValid() && rb.gameObject.scene.isLoaded)
                .ToArray();
            var colliders = Resources.FindObjectsOfTypeAll<Collider>()
                .Where(col => col != null && col.gameObject.scene.IsValid() && col.gameObject.scene.isLoaded)
                .ToArray();
            var meshColliders = Resources.FindObjectsOfTypeAll<MeshCollider>()
                .Where(col => col != null && col.gameObject.scene.IsValid() && col.gameObject.scene.isLoaded)
                .ToArray();

            int rigidbodyWithoutCollider = 0;
            int dynamicNonConvexMeshCollider = 0;
            var auditWarnings = new List<string>();

            foreach (var body in rigidbodies)
            {
                var attached = body.GetComponentsInChildren<Collider>(true);
                if (attached.Length == 0)
                {
                    rigidbodyWithoutCollider++;
                    auditWarnings.Add("Rigidbody without Collider: " + body.gameObject.name);
                }

                if (!body.isKinematic)
                {
                    foreach (var meshCollider in attached.OfType<MeshCollider>())
                    {
                        if (!meshCollider.convex)
                        {
                            dynamicNonConvexMeshCollider++;
                            auditWarnings.Add("Dynamic Rigidbody uses non-convex MeshCollider: " + body.gameObject.name);
                        }
                    }
                }
            }

            var pipeline = GraphicsSettings.currentRenderPipeline;
            reply.ok = !physicsAudit || (rigidbodyWithoutCollider == 0 && dynamicNonConvexMeshCollider == 0);
            reply.summary = physicsAudit
                ? (reply.ok ? "Physics audit passed" : "Physics audit found issues")
                : "Scene summary ready";
            reply.activeScene = SceneManager.GetActiveScene().path;
            reply.renderPipeline = pipeline != null ? pipeline.GetType().Name : "Built-in";
            reply.gameObjectCount = sceneObjects.Length;
            reply.activeGameObjectCount = sceneObjects.Count(go => go.activeInHierarchy);
            reply.rigidbodyCount = rigidbodies.Length;
            reply.colliderCount = colliders.Length;
            reply.meshColliderCount = meshColliders.Length;
            reply.rendererCount = Resources.FindObjectsOfTypeAll<Renderer>()
                .Count(r => r != null && r.gameObject.scene.IsValid() && r.gameObject.scene.isLoaded);
            reply.cameraCount = Resources.FindObjectsOfTypeAll<Camera>()
                .Count(cam => cam != null && cam.gameObject.scene.IsValid() && cam.gameObject.scene.isLoaded);
            reply.lightCount = Resources.FindObjectsOfTypeAll<Light>()
                .Count(light => light != null && light.gameObject.scene.IsValid() && light.gameObject.scene.isLoaded);
            reply.canvasCount = Resources.FindObjectsOfTypeAll<Canvas>()
                .Count(canvas => canvas != null && canvas.gameObject.scene.IsValid() && canvas.gameObject.scene.isLoaded);
            reply.rigidbodyWithoutColliderCount = rigidbodyWithoutCollider;
            reply.dynamicNonConvexMeshColliderCount = dynamicNonConvexMeshCollider;
            reply.auditWarnings = auditWarnings.Take(200).ToArray();
        }

        private static bool Finite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private static bool Finite(Vector3 value)
        {
            return Finite(value.x) && Finite(value.y) && Finite(value.z);
        }

        private static void SpatialAudit(Reply reply)
        {
            var transforms = Resources.FindObjectsOfTypeAll<Transform>()
                .Where(t => t != null && t.gameObject.scene.IsValid() && t.gameObject.scene.isLoaded)
                .ToArray();
            var renderers = Resources.FindObjectsOfTypeAll<Renderer>()
                .Where(r => r != null && r.gameObject.scene.IsValid() && r.gameObject.scene.isLoaded)
                .ToArray();
            var colliders = Resources.FindObjectsOfTypeAll<Collider>()
                .Where(c => c != null && c.gameObject.scene.IsValid() && c.gameObject.scene.isLoaded)
                .ToArray();

            int mirrored = 0;
            int nearZero = 0;
            int extremeScale = 0;
            int extremePosition = 0;
            int nonFinite = 0;
            int invertedRoots = 0;
            var auditWarnings = new List<string>();

            foreach (var transform in transforms)
            {
                Vector3 position = transform.position;
                Vector3 scale = transform.lossyScale;
                Vector3 euler = transform.eulerAngles;

                if (!Finite(position) || !Finite(scale) || !Finite(euler))
                {
                    nonFinite++;
                    auditWarnings.Add("Non-finite transform: " + Hierarchy(transform));
                    continue;
                }

                if ((scale.x * scale.y * scale.z) < 0f)
                {
                    mirrored++;
                    auditWarnings.Add("Mirrored transform (negative determinant): " + Hierarchy(transform));
                }
                if (Mathf.Abs(scale.x) < 0.0001f || Mathf.Abs(scale.y) < 0.0001f || Mathf.Abs(scale.z) < 0.0001f)
                {
                    nearZero++;
                    auditWarnings.Add("Near-zero world scale: " + Hierarchy(transform));
                }
                if (Mathf.Abs(scale.x) > 10000f || Mathf.Abs(scale.y) > 10000f || Mathf.Abs(scale.z) > 10000f)
                {
                    extremeScale++;
                    auditWarnings.Add("Extreme world scale (>10000): " + Hierarchy(transform));
                }
                if (Mathf.Abs(position.x) > 1000000f || Mathf.Abs(position.y) > 1000000f || Mathf.Abs(position.z) > 1000000f)
                {
                    extremePosition++;
                    auditWarnings.Add("Extreme world position (>1,000,000): " + Hierarchy(transform));
                }
                if (transform.parent == null && Vector3.Dot(transform.up, Vector3.up) < -0.5f)
                {
                    invertedRoots++;
                    auditWarnings.Add("Root transform is upside down: " + Hierarchy(transform));
                }
            }

            bool hasBounds = false;
            Bounds worldBounds = default;
            foreach (var renderer in renderers)
            {
                if (!renderer.enabled || !renderer.gameObject.activeInHierarchy) continue;
                if (!hasBounds)
                {
                    worldBounds = renderer.bounds;
                    hasBounds = true;
                }
                else
                {
                    worldBounds.Encapsulate(renderer.bounds);
                }
            }

            Camera camera = Camera.main;
            if (camera == null) camera = Camera.allCameras.FirstOrDefault();
            int cameraInsideCollider = 0;
            int cameraBoundsContainingCollider = 0;
            bool cameraBelowBounds = false;
            var cameraInsideNames = new List<string>();
            var cameraBoundsContainingNames = new List<string>();
            if (camera != null)
            {
                Vector3 cameraPosition = camera.transform.position;
                reply.cameraPosition = cameraPosition;
                reply.cameraEulerAngles = camera.transform.eulerAngles;

                foreach (var collider in colliders)
                {
                    if (!collider.enabled || !collider.gameObject.activeInHierarchy) continue;

                    if (collider.bounds.Contains(cameraPosition))
                    {
                        cameraBoundsContainingCollider++;
                        cameraBoundsContainingNames.Add(Hierarchy(collider.transform));
                    }

                    Vector3 closest = collider.ClosestPoint(cameraPosition);
                    if ((closest - cameraPosition).sqrMagnitude <= 0.000001f)
                    {
                        cameraInsideCollider++;
                        cameraInsideNames.Add(Hierarchy(collider.transform));
                    }
                }

                var hits = Physics.RaycastAll(
                        cameraPosition + Vector3.up * 0.25f,
                        Vector3.down,
                        10000f,
                        ~0,
                        QueryTriggerInteraction.Ignore)
                    .Where(hit =>
                        hit.collider != null &&
                        hit.collider.enabled &&
                        hit.collider.gameObject.activeInHierarchy &&
                        !hit.collider.transform.IsChildOf(camera.transform.root))
                    .OrderBy(hit => hit.distance)
                    .ToArray();

                if (hits.Length > 0)
                {
                    var ground = hits[0];
                    reply.cameraGroundPoint = ground.point;
                    reply.cameraGroundCollider = Hierarchy(ground.collider.transform);
                    reply.cameraGroundDistance = Mathf.Max(0f, cameraPosition.y - ground.point.y);
                    if (reply.cameraGroundDistance > 5f)
                    {
                        auditWarnings.Add(
                            "Active camera is " +
                            reply.cameraGroundDistance.ToString("F2") +
                            " m above the nearest collider below (" +
                            reply.cameraGroundCollider +
                            ").");
                    }
                }
                else
                {
                    auditWarnings.Add("No collider was found below the active camera within 10,000 m.");
                }

                if (hasBounds)
                {
                    float margin = Mathf.Max(1f, worldBounds.size.y * 0.02f);
                    cameraBelowBounds = cameraPosition.y < worldBounds.min.y - margin;
                    if (cameraBelowBounds)
                    {
                        auditWarnings.Add("Active camera is below the visible renderer bounds.");
                    }
                }
                if (cameraInsideCollider > 0)
                {
                    auditWarnings.Add(
                        "Active camera is physically inside " +
                        cameraInsideCollider +
                        " collider(s): " +
                        string.Join(", ", cameraInsideNames.Take(8).ToArray()));
                }
            }

            reply.activeScene = SceneManager.GetActiveScene().path;
            reply.gameObjectCount = transforms.Length;
            reply.rendererCount = renderers.Length;
            reply.colliderCount = colliders.Length;
            reply.mirroredTransformCount = mirrored;
            reply.nearZeroScaleCount = nearZero;
            reply.extremeScaleCount = extremeScale;
            reply.extremePositionCount = extremePosition;
            reply.nonFiniteTransformCount = nonFinite;
            reply.invertedRootCount = invertedRoots;
            reply.cameraInsideColliderCount = cameraInsideCollider;
            reply.cameraBoundsContainingColliderCount = cameraBoundsContainingCollider;
            reply.cameraInsideColliderNames = cameraInsideNames.Take(50).ToArray();
            reply.cameraBoundsContainingColliderNames = cameraBoundsContainingNames.Take(50).ToArray();
            reply.cameraBelowRenderBounds = cameraBelowBounds;
            if (hasBounds)
            {
                reply.renderBoundsCenter = worldBounds.center;
                reply.renderBoundsSize = worldBounds.size;
            }
            reply.auditWarnings = auditWarnings.Take(200).ToArray();

            bool severe = nonFinite > 0 || extremePosition > 0;
            reply.ok = !severe;
            reply.summary = severe
                ? "Spatial audit found invalid world transforms"
                : (auditWarnings.Count > 0 ? "Spatial audit completed with warnings" : "Spatial audit passed");
        }

        private static void OpenScene(Command command, Reply reply)
        {
            if (string.IsNullOrWhiteSpace(command.scenePath) ||
                !command.scenePath.StartsWith("Assets/", StringComparison.Ordinal) ||
                !command.scenePath.EndsWith(".unity", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("scenePath must be a project-relative Assets/*.unity path");

            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            string fullPath = Path.GetFullPath(Path.Combine(projectRoot, command.scenePath));
            if (!fullPath.StartsWith(projectRoot, StringComparison.OrdinalIgnoreCase) || !File.Exists(fullPath))
                throw new InvalidOperationException("Scene does not exist inside the project");

            var active = SceneManager.GetActiveScene();
            if (active.IsValid() && active.isDirty)
                throw new InvalidOperationException("Active scene has unsaved changes");

            UnityEditor.SceneManagement.EditorSceneManager.OpenScene(command.scenePath, UnityEditor.SceneManagement.OpenSceneMode.Single);
            reply.summary = "Scene opened: " + command.scenePath;
        }

        private static string ValidateProjectAssetPath(string raw, string[] extensions, string field)
        {
            string assetPath = (raw ?? string.Empty).Replace('\\', '/').Trim();
            if (string.IsNullOrWhiteSpace(assetPath) ||
                !assetPath.StartsWith("Assets/", StringComparison.Ordinal) ||
                assetPath.Contains("../") || assetPath.Contains("/.."))
                throw new InvalidOperationException(field + " must stay inside Assets/");
            string extension = Path.GetExtension(assetPath).ToLowerInvariant();
            if (!extensions.Contains(extension))
                throw new InvalidOperationException(field + " has an unsupported extension");
            return assetPath;
        }

        private static string ProjectFullPath(string assetPath, bool requireFile)
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            string rootPrefix = Path.GetFullPath(projectRoot + Path.DirectorySeparatorChar);
            string fullPath = Path.GetFullPath(Path.Combine(projectRoot, assetPath.Replace('/', Path.DirectorySeparatorChar)));
            if (!fullPath.StartsWith(rootPrefix, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Asset path escaped the Unity project");
            if (requireFile && !File.Exists(fullPath))
                throw new InvalidOperationException("Asset does not exist inside the project");
            return fullPath;
        }

        private static GameObject ImportModel(string assetPath)
        {
            AssetDatabase.ImportAsset(
                assetPath,
                ImportAssetOptions.ForceUpdate | ImportAssetOptions.ForceSynchronousImport);
            AssetImporter importer = AssetImporter.GetAtPath(assetPath);
            if (importer == null)
                throw new InvalidOperationException("Unity did not create an AssetImporter for the model");
            GameObject modelRoot = AssetDatabase.LoadAssetAtPath<GameObject>(assetPath);
            if (modelRoot == null)
                throw new InvalidOperationException("Unity did not import the model as a GameObject");
            return modelRoot;
        }

        private static HashSet<Mesh> ModelMeshes(GameObject modelRoot)
        {
            var meshes = new HashSet<Mesh>();
            foreach (var filter in modelRoot.GetComponentsInChildren<MeshFilter>(true))
                if (filter.sharedMesh != null) meshes.Add(filter.sharedMesh);
            foreach (var renderer in modelRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                if (renderer.sharedMesh != null) meshes.Add(renderer.sharedMesh);
            return meshes;
        }

        private static void AssetModelAudit(Command command, Reply reply)
        {
            string assetPath = ValidateProjectAssetPath(
                command.assetPath,
                new[] { ".fbx", ".obj", ".blend", ".glb", ".gltf" },
                "assetPath");
            ProjectFullPath(assetPath, true);
            GameObject modelRoot = ImportModel(assetPath);
            AssetImporter importer = AssetImporter.GetAtPath(assetPath);
            var meshes = ModelMeshes(modelRoot);

            int vertices = 0;
            long triangles = 0;
            int blendShapes = 0;
            foreach (var mesh in meshes)
            {
                vertices += mesh.vertexCount;
                blendShapes += mesh.blendShapeCount;
                for (int subMesh = 0; subMesh < mesh.subMeshCount; subMesh++)
                    triangles += (long)mesh.GetIndexCount(subMesh) / 3L;
            }

            var materials = new HashSet<Material>();
            foreach (var renderer in modelRoot.GetComponentsInChildren<Renderer>(true))
                foreach (var material in renderer.sharedMaterials)
                    if (material != null) materials.Add(material);

            var skinnedRenderers = modelRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            var bones = new HashSet<Transform>();
            foreach (var renderer in skinnedRenderers)
                foreach (var bone in renderer.bones)
                    if (bone != null) bones.Add(bone);

            var clips = AssetDatabase.LoadAllAssetsAtPath(assetPath)
                .OfType<AnimationClip>()
                .Where(clip => clip != null && !clip.name.StartsWith("__preview__", StringComparison.Ordinal))
                .ToArray();

            reply.assetPath = assetPath;
            reply.assetImporterType = importer.GetType().FullName;
            reply.modelMeshCount = meshes.Count;
            reply.modelVertexCount = vertices;
            reply.modelTriangleCount = triangles > int.MaxValue ? int.MaxValue : (int)triangles;
            reply.modelMaterialCount = materials.Count;
            reply.modelAnimationClipCount = clips.Length;
            reply.modelBoneCount = bones.Count;
            reply.modelBlendShapeCount = blendShapes;
            reply.modelLodGroupCount = modelRoot.GetComponentsInChildren<LODGroup>(true).Length;

            var modelImporter = importer as ModelImporter;
            reply.modelImporterPresent = modelImporter != null;
            if (modelImporter != null)
            {
                reply.modelGlobalScale = modelImporter.globalScale;
                reply.modelReadable = modelImporter.isReadable;
                reply.modelImportAnimation = modelImporter.importAnimation;
                reply.modelAnimationType = modelImporter.animationType.ToString();
                reply.modelMeshCompression = modelImporter.meshCompression.ToString();
            }

            reply.ok = meshes.Count > 0 && vertices > 0 && triangles > 0;
            reply.summary = reply.ok
                ? "Unity model import audit passed"
                : "Unity imported the asset but no usable mesh geometry was found";
        }

        private static bool TryLodLevel(string name, out int level)
        {
            level = -1;
            if (string.IsNullOrWhiteSpace(name)) return false;
            int marker = name.LastIndexOf("_LOD", StringComparison.OrdinalIgnoreCase);
            if (marker < 0 || marker + 4 >= name.Length) return false;
            string suffix = name.Substring(marker + 4);
            if (suffix.Any(ch => !char.IsDigit(ch))) return false;
            return int.TryParse(suffix, out level) && level >= 0 && level <= 9;
        }

        private static bool RendererLodLevel(Renderer renderer, Transform root, out int level)
        {
            Transform current = renderer.transform;
            while (current != null)
            {
                if (TryLodLevel(current.name, out level)) return true;
                if (current == root) break;
                current = current.parent;
            }
            level = -1;
            return false;
        }

        private static Dictionary<int, List<Renderer>> LodRendererGroups(GameObject root)
        {
            var groups = new Dictionary<int, List<Renderer>>();
            var unassigned = new List<string>();
            foreach (var renderer in root.GetComponentsInChildren<Renderer>(true))
            {
                if (!RendererLodLevel(renderer, root.transform, out int level))
                {
                    unassigned.Add(Hierarchy(renderer.transform));
                    continue;
                }
                if (!groups.TryGetValue(level, out var bucket))
                {
                    bucket = new List<Renderer>();
                    groups[level] = bucket;
                }
                bucket.Add(renderer);
            }
            if (unassigned.Count > 0)
                throw new InvalidOperationException(
                    "Every renderer must belong to a _LOD<n> object/hierarchy. Unassigned: " +
                    string.Join(", ", unassigned.Take(8).ToArray()));
            return groups;
        }

        private static float[] LodThresholds(float[] requested, int levelCount)
        {
            float[] result;
            if (requested == null || requested.Length == 0)
            {
                result = new float[levelCount];
                float value = 0.60f;
                for (int index = 0; index < levelCount; index++)
                {
                    result[index] = Mathf.Max(0.01f, value);
                    value *= 0.5f;
                }
            }
            else
            {
                if (requested.Length != levelCount)
                    throw new InvalidOperationException("lodThresholds count must match detected LOD level count");
                result = requested.ToArray();
            }
            for (int index = 0; index < result.Length; index++)
            {
                if (!Finite(result[index]) || result[index] < 0.01f || result[index] > 0.99f)
                    throw new InvalidOperationException("LOD transition heights must be between 0.01 and 0.99");
                if (index > 0 && result[index] >= result[index - 1])
                    throw new InvalidOperationException("LOD transition heights must be strictly descending");
            }
            return result;
        }

        private static void AssetLodPrefabBuild(Command command, Reply reply)
        {
            string assetPath = ValidateProjectAssetPath(
                command.assetPath,
                new[] { ".fbx", ".obj", ".blend", ".glb", ".gltf" },
                "assetPath");
            string prefabPath = ValidateProjectAssetPath(
                command.prefabPath,
                new[] { ".prefab" },
                "prefabPath");
            ProjectFullPath(assetPath, true);
            string prefabFullPath = ProjectFullPath(prefabPath, false);
            string prefabDirectory = Path.GetDirectoryName(prefabFullPath);
            if (string.IsNullOrWhiteSpace(prefabDirectory) || !Directory.Exists(prefabDirectory))
                throw new InvalidOperationException("prefabPath parent directory must already exist inside Assets/");
            if (AssetDatabase.LoadMainAssetAtPath(prefabPath) != null && !command.overwrite)
                throw new InvalidOperationException("Prefab already exists; set overwrite=true explicitly");

            GameObject modelRoot = ImportModel(assetPath);
            if (modelRoot.GetComponentsInChildren<LODGroup>(true).Length > 0)
                throw new InvalidOperationException("Source model already contains LODGroup components");
            if (modelRoot.GetComponentsInChildren<SkinnedMeshRenderer>(true).Any(r => r.bones != null && r.bones.Length > 0))
                throw new InvalidOperationException("Static LOD prefab generation refuses skinned models");
            if (ModelMeshes(modelRoot).Any(mesh => mesh.blendShapeCount > 0))
                throw new InvalidOperationException("Static LOD prefab generation refuses blend shapes");

            var sourceGroups = LodRendererGroups(modelRoot);
            if (sourceGroups.Count < 2 || !sourceGroups.ContainsKey(0))
                throw new InvalidOperationException("Static LOD prefab needs at least contiguous _LOD0 and _LOD1 renderer groups");
            int maxLevel = sourceGroups.Keys.Max();
            for (int level = 0; level <= maxLevel; level++)
                if (!sourceGroups.ContainsKey(level))
                    throw new InvalidOperationException("LOD levels must be contiguous from _LOD0 through _LOD" + maxLevel);
            int levelCount = maxLevel + 1;
            float[] thresholds = LodThresholds(command.lodThresholds, levelCount);

            GameObject instance = null;
            try
            {
                instance = PrefabUtility.InstantiatePrefab(modelRoot) as GameObject;
                if (instance == null)
                    throw new InvalidOperationException("Could not instantiate imported model for LOD prefab creation");
                instance.name = Path.GetFileNameWithoutExtension(prefabPath);
                var instanceGroups = LodRendererGroups(instance);
                var lods = new LOD[levelCount];
                int rendererTotal = 0;
                for (int level = 0; level < levelCount; level++)
                {
                    Renderer[] renderers = instanceGroups[level].ToArray();
                    rendererTotal += renderers.Length;
                    lods[level] = new LOD(thresholds[level], renderers);
                }
                var lodGroup = instance.AddComponent<LODGroup>();
                lodGroup.fadeMode = LODFadeMode.None;
                lodGroup.SetLODs(lods);
                lodGroup.RecalculateBounds();

                GameObject saved = PrefabUtility.SaveAsPrefabAsset(instance, prefabPath);
                if (saved == null)
                    throw new InvalidOperationException("Unity failed to save the LOD prefab");
                AssetDatabase.SaveAssets();
                AssetDatabase.ImportAsset(prefabPath, ImportAssetOptions.ForceSynchronousImport);

                reply.assetPath = assetPath;
                reply.prefabPath = prefabPath;
                reply.lodLevelCount = levelCount;
                reply.lodRendererCount = rendererTotal;
                reply.lodTransitionHeights = thresholds;
                reply.ok = true;
                reply.summary = "Static Unity LODGroup prefab created";
            }
            finally
            {
                if (instance != null) UnityEngine.Object.DestroyImmediate(instance);
            }
        }

        private static string Hierarchy(Transform transform)
        {
            string path = transform.name;
            while (transform.parent != null) { transform = transform.parent; path = transform.name + "/" + path; }
            return path;
        }
        private static void Capture(Command command, Reply reply)
        {
            if (string.IsNullOrWhiteSpace(command.outputPath) || !Path.IsPathRooted(command.outputPath))
                throw new InvalidOperationException("Capture needs an absolute output path");
            Camera camera = Camera.main;
            if (camera == null) camera = Camera.allCameras.FirstOrDefault();
            if (camera == null) throw new InvalidOperationException("No active scene camera; create or enable one");
            int width = Mathf.Clamp(command.width, 64, 3840), height = Mathf.Clamp(command.height, 64, 2160);
            var target = RenderTexture.GetTemporary(width, height, 24);
            var previousTarget = camera.targetTexture;
            var previousActive = RenderTexture.active;
            Texture2D image = null;
            try
            {
                camera.targetTexture = target;
                camera.Render();
                RenderTexture.active = target;
                image = new Texture2D(width, height, TextureFormat.RGB24, false);
                image.ReadPixels(new Rect(0, 0, width, height), 0, 0);
                image.Apply();
                Directory.CreateDirectory(Path.GetDirectoryName(command.outputPath));
                File.WriteAllBytes(command.outputPath, image.EncodeToPNG());
            }
            finally
            {
                camera.targetTexture = previousTarget;
                RenderTexture.active = previousActive;
                if (image != null) UnityEngine.Object.DestroyImmediate(image);
                RenderTexture.ReleaseTemporary(target);
            }
            var transforms = Resources.FindObjectsOfTypeAll<Transform>()
                .Where(t => t.gameObject.scene.IsValid() && t.gameObject.scene.isLoaded).ToArray();
            var snapshot = new Snapshot { capturedAtUtc = DateTime.UtcNow.ToString("o"),
                scene = SceneManager.GetActiveScene().path, camera = camera.name,
                playing = EditorApplication.isPlaying, objectCount = transforms.Length,
                objectsTruncated = transforms.Length > 1000,
                objects = transforms.Take(1000).Select(t => new SceneObject { name = t.name,
                    path = Hierarchy(t), active = t.gameObject.activeInHierarchy,
                    position = t.position, rotation = t.eulerAngles, scale = t.lossyScale }).ToArray() };
            reply.artifact = command.outputPath;
            reply.snapshotPath = Path.ChangeExtension(command.outputPath, ".json");
            Write(reply.snapshotPath, snapshot);
            reply.summary = "Scene camera captured with hierarchy snapshot (screen-space overlay UI excluded)";
        }
    }
}
#endif