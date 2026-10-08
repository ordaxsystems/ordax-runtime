"""Optional real-binary smoke test for the official Codex App Server.

Run from an isolated test checkout with:
    CODEX_APP_SERVER_BINARY=/absolute/path/to/codex.exe python -m unittest \
        tests.test_codex_app_server_official_smoke -v

An official binary is never downloaded automatically by this test.
The test does not authenticate, submit prompts or execute user projects.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from ordax_studio.codex_app_server import CodexAppServerTransport


@unittest.skipUnless(os.getenv("CODEX_APP_SERVER_BINARY"), "official Codex executable not supplied")
class OfficialCodexAppServerSmokeTests(unittest.TestCase):
    def test_real_initialize_config_read_and_shutdown(self) -> None:
        binary = Path(os.environ["CODEX_APP_SERVER_BINARY"])
        self.assertTrue(binary.is_file(), "CODEX_APP_SERVER_BINARY must be a real executable file")
        with tempfile.TemporaryDirectory(prefix="ordax-codex-isolated-") as codex_home:
            # Do not expose the user account's existing Codex login, tokens,
            # unrelated environment secrets or checkout to this probe.
            allowed = (
                "PATH", "PATHEXT", "SystemRoot", "WINDIR", "TEMP", "TMP",
                "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROCESSOR_ARCHITECTURE",
            )
            environment = {key: os.environ[key] for key in allowed if key in os.environ}
            environment["CODEX_HOME"] = codex_home
            with CodexAppServerTransport(
                [str(binary), "app-server"], environment=environment, timeout=20.0
            ) as transport:
                transport.start(version="0.4.4")
                result = transport.request("config/read", {"includeLayers": False})
                self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
