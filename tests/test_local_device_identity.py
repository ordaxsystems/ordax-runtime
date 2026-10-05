from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from ordax_dev_agent.local_device_identity import (
    LOCAL_DEVICE_ID_FILE,
    LocalDeviceIdentityError,
    load_or_create_local_device_id,
    resolve_windows_runtime_device_identity,
)


class LocalDeviceIdentityTests(unittest.TestCase):
    def test_identity_is_created_once_and_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            first = load_or_create_local_device_id(state)
            second = load_or_create_local_device_id(state)
            self.assertEqual(first, second)
            self.assertEqual(str(uuid.UUID(first)), first)
            self.assertEqual(first, (state / LOCAL_DEVICE_ID_FILE).read_text(encoding="utf-8").strip())

    def test_pairing_does_not_replace_local_studio_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            before = resolve_windows_runtime_device_identity(
                SimpleNamespace(state_dir=state, device_id=None)
            )
            paired = str(uuid.uuid4())
            after = resolve_windows_runtime_device_identity(
                SimpleNamespace(state_dir=state, device_id=paired)
            )

            self.assertEqual(before.local_device_id, after.local_device_id)
            self.assertIsNone(before.paired_device_id)
            self.assertEqual(paired, after.paired_device_id)
            self.assertEqual(
                frozenset({before.local_device_id}),
                after.local_studio_target_ids,
            )
            self.assertNotIn(paired, after.local_studio_target_ids)

    def test_invalid_existing_identity_is_preserved_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            target = state / LOCAL_DEVICE_ID_FILE
            target.write_text("not-a-device-id\n", encoding="utf-8")
            with self.assertRaisesRegex(
                LocalDeviceIdentityError,
                "LOCAL_DEVICE_ID_INVALID_PRESERVED",
            ):
                load_or_create_local_device_id(state)
            self.assertEqual("not-a-device-id\n", target.read_text(encoding="utf-8"))

    def test_local_identity_is_not_derived_from_remote_device_id(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            paired = str(uuid.uuid4())
            identity = resolve_windows_runtime_device_identity(
                SimpleNamespace(state_dir=state, device_id=paired)
            )
            self.assertNotEqual(paired, identity.local_device_id)
            self.assertEqual(paired, identity.paired_device_id)


if __name__ == "__main__":
    unittest.main()
