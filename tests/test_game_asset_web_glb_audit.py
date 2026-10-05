import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_engine_export_actions import ENGINE_EXPORT_SCHEMA


JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def make_glb(document: dict, binary: bytes = b"\x00\x00\x00\x00") -> bytes:
    json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    binary += b"\x00" * ((4 - len(binary) % 4) % 4)
    chunks = struct.pack("<II", len(json_bytes), JSON_CHUNK) + json_bytes
    if binary:
        chunks += struct.pack("<II", len(binary), BIN_CHUNK) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


class GameAssetWebGlbAuditTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        project.mkdir(parents=True, exist_ok=True)
        return AgentConfig(
            agent_name="test-agent",
            poll_seconds=1.0,
            state_dir=root / "state",
            agent_repo_path=root / "agent",
            hordax_path=project,
            bridge_path=root / "bridge",
            projects={
                "game": {
                    "path": str(project),
                    "apps": ["blender", "unity"],
                    "allowed_branches": [],
                }
            },
            default_project="game",
        )

    def write_export(self, root: Path, document: dict) -> Path:
        project = root / "project"
        source = project / "staging" / "asset.blend"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"blend-source")
        artifact = project / "exports" / "asset.glb"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(make_glb(document))
        manifest = Path(str(artifact) + ".ordax.json")
        manifest.write_text(
            json.dumps(
                {
                    "schema": ENGINE_EXPORT_SCHEMA,
                    "project": "game",
                    "engine": "web",
                    "created_at": "2026-09-25T00:00:00+00:00",
                    "source": {
                        "path": source.relative_to(project).as_posix(),
                        "bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    },
                    "artifact": {
                        "path": artifact.relative_to(project).as_posix(),
                        "format": "glb",
                        "bytes": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                    "profile": {"format": "glb", "purpose": "web test"},
                }
            ),
            encoding="utf-8",
        )
        return artifact

    def test_registry_exposes_web_glb_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.web_glb_audit", status.data["actions"])

    def test_self_contained_glb_passes_structural_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.write_export(
                root,
                {
                    "asset": {"version": "2.0", "generator": "test-suite"},
                    "scenes": [{"nodes": [0]}],
                    "nodes": [{"mesh": 0}],
                    "meshes": [{"primitives": []}],
                    "buffers": [{"byteLength": 4}],
                },
            )
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.web_glb_audit",
                {"project": "game", "artifact_path": "exports/asset.glb"},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["container_valid"])
            self.assertTrue(result.data["glb"]["self_contained"])
            self.assertEqual("2.0", result.data["glb"]["asset_version"])
            self.assertEqual(1, result.data["glb"]["meshes"])
            self.assertFalse(result.data["validated_in_browser"])

    def test_external_uri_is_rejected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.write_export(
                root,
                {
                    "asset": {"version": "2.0"},
                    "buffers": [{"byteLength": 4, "uri": "mesh.bin"}],
                },
            )
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.web_glb_audit",
                {"project": "game", "artifact_path": "exports/asset.glb"},
            )
            self.assertFalse(result.ok)
            self.assertIn("external buffer/image URIs", result.summary)

    def test_invalid_glb_length_is_rejected_after_provenance_update(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = self.write_export(root, {"asset": {"version": "2.0"}})
            body = bytearray(artifact.read_bytes())
            struct.pack_into("<I", body, 8, len(body) + 4)
            artifact.write_bytes(bytes(body))
            manifest = Path(str(artifact) + ".ordax.json")
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["artifact"]["bytes"] = artifact.stat().st_size
            data["artifact"]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(data), encoding="utf-8")

            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.web_glb_audit",
                {"project": "game", "artifact_path": "exports/asset.glb"},
            )
            self.assertFalse(result.ok)
            self.assertIn("declared length", result.summary)


if __name__ == "__main__":
    unittest.main()
