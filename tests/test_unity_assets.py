import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.unity_assets import asset_inventory, import_project_asset


class UnityAssetInventoryTests(unittest.TestCase):
    def test_inventory_finds_imported_asset_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            (root/"Assets"/"Pure Nature 2"/"Islands"/"Prefabs").mkdir(parents=True)
            (root/"Assets"/"Pure Nature 2"/"Islands"/"Prefabs"/"Palm.prefab").write_text("x",encoding="utf-8")
            (root/"Assets"/"Other.mat").write_text("x",encoding="utf-8")
            report=asset_inventory(root,terms=["pure nature","islands"])
            self.assertEqual(1,report["matched_files"])
            self.assertEqual("prefab",report["matches"][0]["kind"])
            self.assertIn("Pure Nature 2",report["matches"][0]["path"])

    def test_inventory_ignores_meta_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            (root/"Assets").mkdir()
            (root/"Assets"/"Tree.prefab").write_text("x",encoding="utf-8")
            (root/"Assets"/"Tree.prefab.meta").write_text("m",encoding="utf-8")
            report=asset_inventory(root)
            self.assertEqual(1,report["total_files"])

    def test_import_project_asset_is_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            (root/"Assets").mkdir()
            source=root/"Artifacts"/"Blender"/"boat.fbx"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"boat-fbx")
            destination=root/"Assets"/"HORDAX"/"Art"/"Benchmarks"/"boat.fbx"

            first=import_project_asset(
                root,
                source_path=source,
                destination_path=destination,
            )
            self.assertTrue(first["imported"])
            self.assertEqual("Assets/HORDAX/Art/Benchmarks/boat.fbx",first["asset_path"])
            self.assertEqual(b"boat-fbx",destination.read_bytes())

            second=import_project_asset(
                root,
                source_path=source,
                destination_path=destination,
            )
            self.assertFalse(second["imported"])
            self.assertTrue(second["already_current"])

    def test_import_project_asset_refuses_destination_outside_assets(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            (root/"Assets").mkdir()
            source=root/"Artifacts"/"boat.fbx"
            source.parent.mkdir()
            source.write_bytes(b"boat-fbx")
            with self.assertRaises(ValueError):
                import_project_asset(
                    root,
                    source_path=source,
                    destination_path=root/"boat.fbx",
                )

    def test_import_project_asset_refuses_overwrite_by_default(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw)
            source=root/"Artifacts"/"boat.fbx"
            destination=root/"Assets"/"boat.fbx"
            source.parent.mkdir(parents=True)
            destination.parent.mkdir(parents=True)
            source.write_bytes(b"new")
            destination.write_bytes(b"old")
            with self.assertRaises(FileExistsError):
                import_project_asset(
                    root,
                    source_path=source,
                    destination_path=destination,
                )


if __name__=="__main__":
    unittest.main()
