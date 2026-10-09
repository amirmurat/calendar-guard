"""Regression checks for independent execution status in Guard descriptions."""
import copy
import unittest

from guard import GuardError, prepare, prepare_execution, check_current, verify, read_execution
from preview_compat import dispatch_prepare


class ExecutionMetadataTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "id": "e1", "status": "confirmed",
            "summary": "English practice",
            "description": "Keep this human note.\n\n[AMIR-CALENDAR-GUARD:v1]\nExisting managed text\n[/AMIR-CALENDAR-GUARD]\n",
            "start": "2026-10-09T18:00:00+05:00",
            "end": "2026-10-09T18:30:00+05:00",
            "attendees": [],
            "transparency": "opaque",
            "color_id": "3",
        }
        self.meta = {
            "schema_version": 1,
            "status": "PLANNED",
            "planned_start": "2026-10-09T09:10:00+05:00",
            "planned_end": "2026-10-09T09:40:00+05:00",
            "actual_start": None,
            "actual_end": None,
            "evidence": None,
        }

    def req(self):
        return {
            "operation": "execution_status",
            "event": copy.deepcopy(self.event),
            "execution": copy.deepcopy(self.meta),
            "context": {
                "complete": True,
                "window_start": "2026-10-09T00:00:00+05:00",
                "window_end": "2026-10-10T00:00:00+05:00",
                "events": [self.event],
            },
        }

    def test_metadata_only_preserves_guard_and_event_fields(self):
        request = self.req()
        plan = dispatch_prepare(request)
        self.assertEqual(list(plan["diff"]), ["description"])
        self.assertEqual(set(plan["connector_payload"]),
                         {"event_id", "update_scope", "description"})
        self.assertEqual(plan["before"]["start"], plan["after"]["start"])
        self.assertEqual(plan["before"]["summary"], plan["after"]["summary"])
        self.assertIn("Existing managed text", plan["after"]["description"])
        self.assertEqual(read_execution(plan["after"]["description"])[0], self.meta)
        self.assertTrue(check_current(plan, request["event"])["ok"])
        self.assertTrue(verify(plan, plan["after"])["ok"])

    def test_change_to_done_retains_original_plan(self):
        first = dispatch_prepare(self.req())
        follow = self.req()
        follow["event"] = first["after"]
        follow["execution"].update({
            "status": "DONE", "actual_start": "2026-10-09T05:30:00+05:00",
            "actual_end": "2026-10-09T06:00:00+05:00",
            "evidence": "User confirmed both boundaries",
        })
        second = dispatch_prepare(follow)
        self.assertEqual(read_execution(second["after"]["description"])[0],
                         follow["execution"])
        self.assertEqual(second["after"]["description"].count("[AMIR-EXECUTION-STATUS:v1]"), 1)
        self.assertIn("Keep this human note.", second["after"]["description"])

    def test_reject_rewrite_of_original_plan(self):
        first = dispatch_prepare(self.req())
        r = self.req()
        r["event"] = first["after"]
        r["execution"]["planned_start"] = "2026-10-09T09:00:00+05:00"
        with self.assertRaisesRegex(GuardError, "immutable"):
            dispatch_prepare(r)

    def test_reject_done_without_evidence(self):
        r = self.req()
        r["execution"]["status"] = "DONE"
        with self.assertRaisesRegex(GuardError, "factual source"):
            dispatch_prepare(r)

    def test_reject_false_completion_from_planned_time(self):
        r = self.req()
        r["execution"]["actual_start"] = "2026-10-09T18:00:00+05:00"
        with self.assertRaisesRegex(GuardError, "Non-completion"):
            dispatch_prepare(r)

    def test_reject_bad_json_marker(self):
        r = self.req()
        r["event"]["description"] += "\n[AMIR-EXECUTION-STATUS:v1]\n{broken}\n[/AMIR-EXECUTION-STATUS]"
        with self.assertRaisesRegex(GuardError, "Malformed"):
            dispatch_prepare(r)

    def test_reject_cancelled_event_and_guests(self):
        r = self.req()
        r["event"]["status"] = "cancelled"
        with self.assertRaisesRegex(GuardError, "Cancelled"):
            dispatch_prepare(r)
        r = self.req()
        r["event"]["attendees"] = [{"email": "invitee@example.org", "is_self": False}]
        with self.assertRaisesRegex(GuardError, "guests"):
            dispatch_prepare(r)

    def test_reject_incomplete_calendar(self):
        r = self.req()
        r["context"]["complete"] = False
        with self.assertRaisesRegex(GuardError, "complete"):
            dispatch_prepare(r)

    def test_reject_stale_snapshot(self):
        r = self.req()
        plan = dispatch_prepare(r)
        current = copy.deepcopy(r["event"])
        current["description"] += "\nOther user changed this."
        with self.assertRaisesRegex(GuardError, "изменилось"):
            check_current(plan, current)

    def test_standard_guard_wont_erase_marker(self):
        self.event["description"] = "Original human note."
        r = self.req()
        first = dispatch_prepare(r)
        old_event = first["after"]
        block = {
            "schema_version": 1, "kind": "practice", "area": "English",
            "task": "speaking", "flexibility": {"movable": True},
            "materials": [], "conditions": [], "deadline": {"kind": "unknown"},
            "notes": None, "provenance": "test",
        }
        normal = prepare({"event": old_event, "block": block,
                          "context": self.req()["context"]})
        self.assertEqual(read_execution(normal["after"]["description"])[0], self.meta)
        self.assertTrue(verify(normal, normal["after"])["ok"])


if __name__ == "__main__":
    unittest.main()
