"""Contract regressions for the existing Native OS credential authority."""
import unittest
from unittest.mock import Mock

from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
from ordax_dev_agent.remote_protocol import DeviceAuthorizationError


class NativeCredentialBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.agent = ProductDeviceControlPlane.__new__(ProductDeviceControlPlane)
        self.agent.device_id = "22222222-2222-4222-8222-222222222222"
        self.agent.device_token = "a" * 64
        self.active = {"device_id": self.agent.device_id, "device_token": self.agent.device_token}
        self.agent._native_credential_provider = lambda: self.active
        self.agent._trusted_ingress = Mock()

    def test_current_identity_is_accepted(self):
        self.agent._assert_native_credential_current()

    def test_rotated_or_revoked_identity_does_not_reach_network(self):
        variants = [
            None,
            {"device_id": self.agent.device_id, "device_token": "b" * 64},
            {"device_id": "33333333-3333-4333-8333-333333333333", "device_token": "a" * 64},
            {"device_id": self.agent.device_id, "device_token": "invalid"},
        ]
        for value in variants:
            with self.subTest(value=value):
                self.active = value
                with self.assertRaises(DeviceAuthorizationError):
                    self.agent._post("heartbeat", {"status": "online"})
                self.agent._trusted_ingress.request.assert_not_called()

    def test_unreadable_identity_is_denied_without_exposing_exception(self):
        def broken():
            raise OSError("private-state-unavailable")
        self.agent._native_credential_provider = broken
        with self.assertRaises(DeviceAuthorizationError) as failed:
            self.agent._post("heartbeat", {"status": "online"})
        self.assertNotIn("private-state-unavailable", str(failed.exception))
        self.agent._trusted_ingress.request.assert_not_called()

    def test_central_session_attestation_rechecks_same_os_identity(self):
        self.assertEqual(self.agent._current_session_device_token(), "a" * 64)
        self.active = {"device_id": self.agent.device_id, "device_token": "b" * 64}
        with self.assertRaises(DeviceAuthorizationError):
            self.agent._current_session_device_token()
        self.agent._trusted_ingress.request.assert_not_called()

    def test_windows_identity_is_not_requeried(self):
        self.agent._native_credential_provider = None
        self.agent._assert_native_credential_current()


if __name__ == "__main__":
    unittest.main()
