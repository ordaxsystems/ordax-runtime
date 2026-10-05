import unittest


class OrdaXDeviceAgentAliasTests(unittest.TestCase):
    def test_product_package_reuses_current_agent_version(self):
        import ordax_dev_agent
        import ordax_device_agent

        self.assertEqual(ordax_device_agent.__version__, ordax_dev_agent.__version__)

    def test_device_agent_entrypoint_delegates_without_forking_runtime(self):
        from ordax_dev_agent.main import main as legacy_main
        from ordax_device_agent.main import main as product_main

        self.assertIs(product_main, legacy_main)

    def test_device_mcp_entrypoint_reuses_same_server(self):
        from ordax_dev_agent.mcp_server import mcp as legacy_mcp
        from ordax_device_agent.mcp_server import mcp as product_mcp
        from ordax_studio.mcp_server import mcp as studio_mcp

        self.assertIs(product_mcp, legacy_mcp)
        self.assertIs(studio_mcp, legacy_mcp)


if __name__ == "__main__":
    unittest.main()
