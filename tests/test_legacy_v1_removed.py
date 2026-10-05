from __future__ import annotations

import unittest
from pathlib import Path


class RetiredProviderRemovalTests(unittest.TestCase):
    def test_runtime_supports_only_cloudflare_v3(self) -> None:
        root = Path(__file__).resolve().parents[1]
        control = (root / "ordax_dev_agent" / "control_plane.py").read_text(
            encoding="utf-8"
        )
        config = (root / "ordax_dev_agent" / "config.py").read_text(
            encoding="utf-8"
        )
        main = (root / "ordax_dev_agent" / "main.py").read_text(encoding="utf-8")
        setup = (root / "ordax_dev_agent" / "device_setup.py").read_text(
            encoding="utf-8"
        )
        bootstrap = (
            root / "scripts" / "windows" / "ordax-agent-bootstrap.ps1"
        ).read_text(encoding="utf-8")
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

        for text in (control, config, main, bootstrap):
            self.assertNotIn("development-v2", text)
            self.assertNotIn("supabase", text.lower())

        self.assertNotIn('"supabase>=', pyproject)
        self.assertNotIn("supabase_url", config)
        self.assertNotIn("development_device_id", config)
        self.assertNotIn("control_plane_identities", setup)

        self.assertIn('protocol != "cloudflare-v3"', control)
        self.assertIn('control_plane_protocol: str = "cloudflare-v3"', config)
        self.assertIn("device_id: str | None", config)
        self.assertIn("DEFAULT_CONTROL_PLANE_URL", config)
        self.assertTrue(
            (root / "ordax_dev_agent" / "cloudflare_control_plane.py").is_file()
        )
        self.assertFalse(
            (root / "ordax_dev_agent" / "development_control_plane.py").exists()
        )

    def test_supabase_backend_and_v2_migration_scripts_are_gone(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertFalse((root / "control-plane" / "supabase").exists())

        for name in (
            "ordax-development-v2-enroll.ps1",
            "ordax-development-v2-recover.ps1",
            "ordax-cloudflare-v3-cutover.ps1",
            "ordax-cloudflare-v3-finalize.ps1",
            "ordax-cloudflare-v3-retirement-status.ps1",
        ):
            self.assertFalse((root / "scripts" / "windows" / name).exists())

        for name in (
            "device-token.development-v2.txt",
            "device-token.txt",
        ):
            credentials = (
                root / "ordax_dev_agent" / "device_credentials.py"
            ).read_text(encoding="utf-8")
            self.assertNotIn(name, credentials)


if __name__ == "__main__":
    unittest.main()
