"""Regression tests for the stateless, read-only schedule preview tool."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from schedule_preview import preview_slots, PreviewError

NOW=datetime(2026,10,8,13,50,tzinfo=ZoneInfo("Asia/Almaty"))

def fixture():
    return {
        "constraints_attested":True,
        "calendar":{"complete":True,"next_page_token":None,
          "observed_at":NOW.isoformat(),
          "window_start":"2026-10-08T00:00:00+05:00",
          "window_end":"2026-10-14T00:00:00+05:00",
          "events":[
            {"id":"moodle","summary":"Moodle",
              "start":"2026-10-08T09:10:00+05:00","end":"2026-10-08T12:10:00+05:00"},
            {"id":"friday","summary":"University",
              "start":"2026-10-09T11:00:00+05:00","end":"2026-10-09T18:00:00+05:00"}]},
        "task":{"id":"Moodle","status":"Not started","duration_minutes":180,
          "earliest_start":"2026-10-10T12:00:00+05:00",
          "finish_before":"2026-10-12T00:00:00+05:00",
          "deadline_kind":"internal","linked_event_id":"moodle",
          "linked_event_movable":True},
        "rules":{"focus_start":"08:30","focus_end":"19:00",
                 "transition_minutes":15,"pre_travel_minutes":20},
        "protected":[{"start":"2026-10-10T12:00:00+05:00",
                       "end":"2026-10-10T13:00:00+05:00"}]
    }

class PreviewTests(unittest.TestCase):
    def test_selects_october_tenth(self):
        p=preview_slots(fixture(),now=NOW)
        self.assertEqual(p["status"],"FEASIBLE")
        self.assertEqual(p["suggestions"][0]["start"],"2026-10-10T13:00+05:00")
        self.assertEqual(p["calendar_writes"],0)
    def test_fails_internal_deadline(self):
        x=fixture();x["task"]["finish_before"]="2026-10-10T13:00:00+05:00"
        self.assertEqual(preview_slots(x,now=NOW)["status"],"NO_FEASIBLE_SLOT")
    def test_rejects_incomplete_page(self):
        x=fixture();x["calendar"]["next_page_token"]="cursor"
        with self.assertRaises(PreviewError):preview_slots(x,now=NOW)
    def test_rejects_stale_calendar(self):
        x=fixture();x["calendar"]["observed_at"]=(NOW-timedelta(hours=2)).isoformat()
        with self.assertRaises(PreviewError):preview_slots(x,now=NOW)
    def test_rejects_unattested_buffers(self):
        x=fixture();x["constraints_attested"]=False
        with self.assertRaises(PreviewError):preview_slots(x,now=NOW)
    def test_rejects_unmovable_event(self):
        x=fixture();x["task"]["linked_event_movable"]=False
        with self.assertRaises(PreviewError):preview_slots(x,now=NOW)
    def test_rejects_completed_tasks(self):
        x=fixture();x["task"]["status"]="Completed"
        with self.assertRaises(PreviewError):preview_slots(x,now=NOW)
    def test_rejects_all_day_without_coverage(self):
        x=fixture();x["calendar"]["events"].append(
            {"id":"day","summary":"Holiday","start":{"date":"2026-10-10"},
             "end":{"date":"2026-10-11"}})
        with self.assertRaises(PreviewError):preview_slots(x,now=NOW)
    def test_rejects_collision(self):
        x=fixture();x["calendar"]["events"].append(
            {"id":"class","summary":"class",
             "start":"2026-10-10T13:00:00+05:00",
             "end":"2026-10-10T19:00:00+05:00"})
        p=preview_slots(x,now=NOW)
        self.assertTrue(all(q["start"][:10]!="2026-10-10" for q in p["suggestions"]))
    def test_cannot_forge_confirmed_deadline(self):
        x=fixture();x["task"]["deadline_kind"]="confirmed"
        with self.assertRaises(PreviewError):preview_slots(x,now=NOW)

if __name__=="__main__":
    unittest.main()
