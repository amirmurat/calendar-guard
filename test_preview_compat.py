"""Compatibility preview tests: no credentials, network or Calendar writes."""
import copy
import unittest
from guard import GuardError
from schedule_preview import PreviewError
from preview_compat import dispatch_prepare
from test_schedule_preview import fixture, NOW
from test_self_attendees import SelfAttendeeRegressionTests


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.payload = {"operation": "preview_schedule", "preview_request": fixture()}

    def test_read_only_preview_works(self):
        out = dispatch_prepare(copy.deepcopy(self.payload), now=NOW)
        self.assertEqual(out["mode"], "READ_ONLY")
        self.assertEqual(out["calendar_writes"], 0)
        self.assertEqual(out["planner_writes"], 0)
        self.assertEqual(out["suggestions"][0]["start"], "2026-10-10T13:00+05:00")
        self.assertNotIn("connector_payload", out)

    def test_legacy_prepare_unchanged(self):
        t = SelfAttendeeRegressionTests("test_self_only_allowed_without_removal")
        t.setUp()
        legacy = t.request()
        result = dispatch_prepare(legacy)
        self.assertEqual(result["after"]["start"], "2026-10-10T13:00:00+05:00")
        self.assertIn("connector_payload", result)

    def test_unknown_mode_rejected(self):
        data = copy.deepcopy(self.payload)
        data["operation"] = "calendar_write"
        with self.assertRaises(GuardError):
            dispatch_prepare(data, now=NOW)

    def test_mixed_write_preview_rejected(self):
        data = copy.deepcopy(self.payload)
        data["event"] = {"id": "fake"}
        with self.assertRaises(GuardError):
            dispatch_prepare(data, now=NOW)

    def test_stale_data_rejected(self):
        data = copy.deepcopy(self.payload)
        data["preview_request"]["calendar"]["observed_at"] = "2026-10-08T08:00:00+05:00"
        with self.assertRaises(PreviewError):
            dispatch_prepare(data, now=NOW)

    def test_missing_full_pages_rejected(self):
        data = copy.deepcopy(self.payload)
        data["preview_request"]["calendar"]["complete"] = False
        with self.assertRaises(PreviewError):
            dispatch_prepare(data, now=NOW)

    def test_forbids_deadline_override(self):
        data = copy.deepcopy(self.payload)
        data["preview_request"]["task"]["finish_before"] = "2026-10-10T13:00:00+05:00"
        result = dispatch_prepare(data, now=NOW)
        self.assertEqual(result["status"], "NO_FEASIBLE_SLOT")


if __name__ == "__main__":
    unittest.main()
