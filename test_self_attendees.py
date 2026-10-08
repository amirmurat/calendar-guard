"""Regression tests for Calendar Guard self-only attendee handling.

Runs with python -m unittest discover -v (no Calendar credentials needed).
"""
import copy
import unittest

from guard import GuardError, check_current, prepare, verify


class SelfAttendeeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "id": "test-moodle-1",
            "summary": "Moodle — Выполнение",
            "description": "Original notes — never lose these.",
            "start": "2026-10-08T09:10:00+05:00",
            "end": "2026-10-08T12:10:00+05:00",
            "attendees": [{"email": "owner@example.test", "is_self": True}],
            "color_id": "3",
            "transparency": "opaque",
        }
        self.block = {
            "schema_version": 1,
            "kind": "work",
            "area": "University",
            "task": "Moodle",
            "flexibility": {"movable": True, "min_minutes": 180, "max_minutes": 180},
            "materials": [],
            "conditions": ["Complete before RK1"],
            "deadline": {"kind": "internal", "value": "2026-10-12"},
            "notes": "Do not treat a calendar event as completed work",
            "provenance": "Regression fixture",
            "result": "Check and submit Moodle assignments",
            "actions": ["Check actual course deadlines", "Complete tasks"],
            "fallback": "Flag as incomplete and replan before RK1",
        }

    def request(self, attendees_marker=True):
        event = copy.deepcopy(self.event)
        if attendees_marker is not True:
            event["attendees"] = attendees_marker
        return {
            "event": event,
            "block": copy.deepcopy(self.block),
            "time": {
                "start": "2026-10-10T13:00:00+05:00",
                "end": "2026-10-10T16:00:00+05:00",
            },
            "context": {
                "complete": True,
                "window_start": "2026-10-08T00:00:00+05:00",
                "window_end": "2026-10-14T00:00:00+05:00",
                "timezone": "Asia/Almaty",
                "events": [event],
                "protected": [],
            },
        }

    def test_self_only_allowed_without_removal(self):
        request = self.request()
        plan = prepare(request)
        self.assertEqual(plan["before"]["attendees"], request["event"]["attendees"])
        self.assertEqual(plan["after"]["attendees"], request["event"]["attendees"])
        self.assertEqual(plan["after"]["color_id"], "3")
        self.assertIn("Original notes", plan["after"]["description"])
        self.assertNotIn("attendees", plan["connector_payload"])
        self.assertTrue(check_current(plan, copy.deepcopy(request["event"]))["ok"])
        self.assertTrue(verify(plan, copy.deepcopy(plan["after"]))["ok"])

    def test_no_attendee_allowed(self):
        self.assertIn("after", prepare(self.request([])))

    def test_null_attendees_allowed(self):
        self.assertIn("after", prepare(self.request(None)))

    def test_external_guest_rejected(self):
        guests = [
            {"email": "owner@example.test", "is_self": True},
            {"email": "guest@example.test", "is_self": False},
        ]
        with self.assertRaisesRegex(GuardError, "участниками"):
            prepare(self.request(guests))

    def test_missing_self_flag_rejected(self):
        with self.assertRaisesRegex(GuardError, "участниками"):
            prepare(self.request([{"email": "owner@example.test"}]))

    def test_nonboolean_self_flag_rejected(self):
        with self.assertRaisesRegex(GuardError, "участниками"):
            prepare(self.request([{"is_self": "true"}]))

    def test_malformed_attendee_rejected(self):
        with self.assertRaises(GuardError):
            prepare(self.request(["owner@example.test"]))

    def test_more_than_one_self_rejected(self):
        with self.assertRaisesRegex(GuardError, "участниками"):
            prepare(self.request([{"is_self": True}, {"is_self": True}]))

    def test_calendar_incomplete_rejected(self):
        request = self.request()
        request["context"]["complete"] = False
        with self.assertRaisesRegex(GuardError, "полным"):
            prepare(request)

    def test_protected_interval_rejected(self):
        request = self.request()
        request["context"]["protected"] = [
            {"start": "2026-10-10T15:00:00+05:00",
             "end": "2026-10-10T15:30:00+05:00", "label": "Rest"}
        ]
        with self.assertRaisesRegex(GuardError, "Нарушено ограничение"):
            prepare(request)


if __name__ == "__main__":
    unittest.main()
