import unittest
from datetime import datetime, timezone
from jc.website_meta import select_current, current_patch

class WebsiteMetaTests(unittest.TestCase):
    def test_only_current_patch_fresh_successful_jungle_rows(self):
        now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
        row = dict(status="ok", patch="16.23", source="opgg", role="jungle",
                   fetched_at="2026-11-23T10:00:00Z", sections=[])
        rows = [row, dict(row, patch="16.22"), dict(row, status="error"),
                dict(row, fetched_at="2026-11-21T10:00:00Z"),
                dict(row, fetched_at="2026-11-24T10:00:00Z"),
                dict(row, role="top")]
        self.assertEqual(select_current({"schema_version": 1, "results": rows}, "16.23", now), [row])
        self.assertEqual(select_current({"schema_version": 1, "results": rows}, "16.24", now), [])
    def test_version(self):
        self.assertEqual(current_patch("16.23.1"), "16.23")
    def test_unknown_schema(self):
        with self.assertRaises(ValueError):
            select_current({"results": []}, "16.23")