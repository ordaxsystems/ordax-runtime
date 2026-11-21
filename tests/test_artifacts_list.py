from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ordax_dev_agent.artifact_actions import ArtifactActions


class _Host(ArtifactActions):
    def __init__(self, root: Path):
        self.project = SimpleNamespace(slug="demo", root=root / "project")
        self.project.root.mkdir(parents=True)
        self.config = SimpleNamespace(state_dir=root / "state")

    def _project(self, _payload):
        return self.project


class ArtifactsListTests(unittest.TestCase):
    def test_lists_only_relative_metadata_from_authorized_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = _Host(root)
            managed = host.config.state_dir / "artifacts" / "demo"
            project_artifacts = host.project.root / "Artifacts"
            managed.mkdir(parents=True)
            project_artifacts.mkdir(parents=True)
            (managed / "preview.png").write_bytes(b"abc")
            (project_artifacts / "exports").mkdir()
            (project_artifacts / "exports" / "scene.glb").write_bytes(b"12345")

            result = host.artifacts_list({"project": "demo"})

            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["items"]), 2)
            self.assertEqual(
                {(item["source"], item["relative_path"]) for item in result.data["items"]},
                {("managed", "preview.png"), ("project", "exports/scene.glb")},
            )
            self.assertTrue(all("path" not in item for item in result.data["items"]))

    def test_enforces_item_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            host = _Host(root)
            managed = host.config.state_dir / "artifacts" / "demo"
            managed.mkdir(parents=True)
            for index in range(3):
                (managed / f"{index}.txt").write_text(str(index), encoding="utf-8")

            result = host.artifacts_list({"project": "demo", "max_items": 2})

            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["items"]), 2)
            self.assertTrue(result.data["truncated"])

    def test_rejects_invalid_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = _Host(Path(tmp))
            result = host.artifacts_list({"project": "demo", "max_items": 0})
            self.assertFalse(result.ok)

    def test_image_sheet_sizes_and_deduplication(self):
        import base64
        import hashlib
        import io
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            host = _Host(Path(tmp))
            root = host.project.root / "Artifacts"
            root.mkdir()
            paths = []
            for index in range(100):
                name = f"{index}.png"
                image = Image.new("RGB", (512, 384), (index, index * 2, 255 - index))
                draw = ImageDraw.Draw(image)
                for x in range(0, 512, 32):
                    draw.line((x, 0, 511-x, 383), fill=(255, 255, 255), width=3)
                image.save(root / name)
                paths.append(name)
            (root / "duplicate.png").write_bytes((root / "0.png").read_bytes())
            for count in (1, 10, 25, 50, 100):
                selected = paths[:count]
                if count > 1:
                    selected = selected[:-1] + ["duplicate.png"]
                result = host.artifact_preview({"project": "demo", "project_artifact_paths": selected,
                                                "max_width": 1200, "max_height": 1000,
                                                "quality": 50})
                self.assertTrue(result.ok, result.summary)
                data = result.data
                self.assertEqual(count, data["image_count"])
                self.assertEqual(count if count == 1 else count - 1, data["unique_image_count"])
                self.assertEqual(selected, [item["project_artifact_path"] for item in data["items"]])
                self.assertEqual(hashlib.sha256((root / "0.png").read_bytes()).hexdigest(),
                                 data["items"][0]["source_sha256"])
                if count > 1:
                    self.assertEqual("I001", data["items"][-1]["tile_id"])
                original = host.artifact_read_chunk({"project": "demo", "project_artifact_path": data["items"][0]["project_artifact_path"]})
                self.assertTrue(original.ok, original.summary)
                self.assertEqual(data["items"][0]["source_sha256"], original.data["chunk_sha256"])
                binary = base64.b64decode(data["base64"])
                self.assertEqual(data["sha256"], hashlib.sha256(binary).hexdigest())
                self.assertLessEqual(len(binary), 262144)
                with Image.open(io.BytesIO(binary)) as sheet:
                    self.assertEqual(tuple(data["thumbnail_size"]), sheet.size)
                print(f"sheet images={count} unique={data['unique_image_count']} source_bytes={data['source_size_bytes']} sheet_bytes={data['size_bytes']} elapsed_ms={data['processing_ms']}")

    def test_image_sheet_preserves_artifact_boundary(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            host = _Host(Path(tmp))
            root = host.project.root / "Artifacts"
            root.mkdir()
            Image.new("RGB", (64, 64)).save(host.project.root / "outside.png")
            for paths in ([], ["../outside.png"], [str(host.project.root / "outside.png")],
                          ["missing.png"], ["x.png"] * 101):
                result = host.artifact_preview({"project": "demo", "project_artifact_paths": paths})
                self.assertFalse(result.ok)

    def test_artifact_chunk_resume_rejects_changed_source(self):
        import base64
        with tempfile.TemporaryDirectory() as tmp:
            host = _Host(Path(tmp))
            root = host.project.root / "Artifacts"
            root.mkdir()
            path = root / "original.png"
            content = bytes(range(256)) * 200
            path.write_bytes(content)
            selector = {"project": "demo", "project_artifact_path": "original.png"}
            first = host.artifact_read_chunk({**selector, "max_bytes": 16384})
            self.assertTrue(first.ok, first.summary)
            resume = {**selector, "offset": first.data["next_offset"],
                      "source_version": first.data["source_version"]}
            second = host.artifact_read_chunk(resume)
            self.assertTrue(second.ok, second.summary)
            self.assertEqual(content[:49152], base64.b64decode(first.data["base64"]) +
                             base64.b64decode(second.data["base64"]))
            path.write_bytes(content + b"changed")
            changed = host.artifact_read_chunk(resume)
            self.assertFalse(changed.ok)
            self.assertEqual("artifact_changed", changed.data["error_code"])


if __name__ == "__main__":
    unittest.main()
