"""The signed Product report must be PostgreSQL JSONB-compatible, including replay."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path

# The packaged Windows Python prioritizes its installed runtime by default.
# Import the checked-out source, never an older installed build.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
from ordax_dev_agent.remote_protocol import canonical_result


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class ProductReportJsonbTextTests(unittest.TestCase):
    def test_canonical_result_escapes_nested_nul_before_digest(self):
        raw_text = "A" + chr(0) + "B"
        already_escaped = "literal-" + chr(92) + "u0000"
        result, digest = canonical_result(ActionResult(True, raw_text, {
            "stdout": raw_text, "nested": [raw_text, {"key": already_escaped}]
        }))
        raw = canonical_bytes(result)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertNotIn(chr(0), json.dumps(result, ensure_ascii=False))
        self.assertNotIn(chr(0), result["summary"])
        self.assertEqual(result["summary"], "A" + chr(92) + "u0000B")
        self.assertEqual(result["data"]["stdout"], "A" + chr(92) + "u0000B")
        self.assertEqual(result["data"]["nested"][1]["key"], already_escaped)

    def test_old_outbox_report_with_nul_reencoded_and_resigned(self):
        plane = ProductDeviceControlPlane.__new__(ProductDeviceControlPlane)
        legacy = {"ok": True, "summary": "done", "data": {
            "stdout": "prefix" + chr(0) + "suffix", "nested": ["ok", chr(0)]
        }}
        raw = canonical_bytes(legacy)
        record = {"report_id": "77777777-7777-4777-8777-777777777777",
                  "result_canonical_b64": base64.b64encode(raw).decode("ascii"),
                  "result_sha256": hashlib.sha256(raw).hexdigest()}
        repaired = plane._wire_report(record)
        recovered = json.loads(base64.b64decode(repaired["result_canonical_b64"]).decode("utf-8"))
        self.assertNotEqual(repaired["result_sha256"], record["result_sha256"])
        self.assertEqual(repaired["report_id"], record["report_id"])
        self.assertEqual(repaired["result_sha256"], hashlib.sha256(canonical_bytes(recovered)).hexdigest())
        self.assertEqual(recovered["data"]["stdout"], "prefix" + chr(92) + "u0000suffix")
        self.assertEqual(recovered["data"]["nested"][-1], chr(92) + "u0000")
        self.assertEqual(plane._wire_report(record), repaired)

    def test_safe_report_bytes_and_digest_remain_identical(self):
        plane = ProductDeviceControlPlane.__new__(ProductDeviceControlPlane)
        data = {"ok": True, "summary": "Windows path", "data": {"path": r"C:\\Users\\App"}}
        raw = canonical_bytes(data)
        original = {"result_canonical_b64": base64.b64encode(raw).decode(),
                    "result_sha256": hashlib.sha256(raw).hexdigest()}
        self.assertEqual(plane._wire_report(original)["result_canonical_b64"], original["result_canonical_b64"])
        self.assertEqual(plane._wire_report(original)["result_sha256"], original["result_sha256"])

    def test_corrupt_old_outbox_digest_is_not_resigned(self):
        plane = ProductDeviceControlPlane.__new__(ProductDeviceControlPlane)
        raw = canonical_bytes({"data": {"stdout": chr(0)}})
        report = {"result_canonical_b64": base64.b64encode(raw).decode(),
                  "result_sha256": "0" * 64}
        with self.assertRaisesRegex(RuntimeError, "integrity failed"):
            plane._wire_report(report)


if __name__ == "__main__":
    unittest.main()
