from __future__ import annotations

import unittest
from pathlib import Path


class RetiredProviderHygieneTests(unittest.TestCase):
    def test_no_retired_provider_references_in_active_tree(self) -> None:
        root = Path(__file__).resolve().parents[1]
        targets = [
            root / "ordax_dev_agent",
            root / "scripts" / "windows",
            root / "docs",
            root / ".github" / "workflows",
            root / "control-plane",
            root / "README.md",
        ]
        forbidden = (
            "development-v2",
            "supabase_url",
            "development_device_id",
            "control-plane/supabase",
            "device-token.development-v2.txt",
        )

        offenders: list[str] = []
        for target in targets:
            files = [target] if target.is_file() else [
                path
                for path in target.rglob("*")
                if path.is_file()
                and path.suffix.lower()
                in {".py", ".ps1", ".md", ".yml", ".yaml", ".toml", ".ts", ".json", ".sh"}
            ]
            for path in files:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                for needle in forbidden:
                    if needle.lower() in text:
                        offenders.append(
                            f"{path.relative_to(root)} contains {needle}"
                        )

        self.assertEqual([], offenders, "\n" + "\n".join(offenders))

    def test_supabase_control_plane_directory_is_absent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / "control-plane" / "supabase").exists())


if __name__ == "__main__":
    unittest.main()
