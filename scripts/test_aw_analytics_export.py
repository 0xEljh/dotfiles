from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

import aw_analytics_export


class PhoneAggregationTests(unittest.TestCase):
    def test_phone_usage_contributes_to_analytics_buckets(self) -> None:
        aggregate = aw_analytics_export.aggregate_day_data(
            {},
            phone_hours={
                10: {
                    "Termux": 1800.0,
                    "Claude": 900.0,
                    "Notion": 600.0,
                    "YouTube": 300.0,
                }
            },
        )

        self.assertEqual(3600.0, aggregate["totals"]["active_time"])
        self.assertEqual(1800.0, aggregate["totals"]["dev_time"])
        self.assertEqual(1500.0, aggregate["totals"]["planning_time"])
        self.assertEqual(900.0, aggregate["totals"]["ai_chat_time"])

        self.assertEqual(1800.0, aggregate["dev_tools"]["Termux"])
        self.assertEqual(900.0, aggregate["planning_apps"]["Claude"])
        self.assertEqual(600.0, aggregate["planning_apps"]["Notion"])
        self.assertEqual(900.0, aggregate["ai_chats"]["Claude"])
        self.assertEqual(300.0, aggregate["top_apps"]["YouTube"])
        self.assertNotIn("YouTube", aggregate["planning_apps"])

    def test_generate_reports_include_phone_only_days(self) -> None:
        today = date(2026, 6, 20)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.combine(today, time(12, 0), tzinfo=tz)

        with (
            patch.object(aw_analytics_export, "datetime", FixedDateTime),
            patch.object(
                aw_analytics_export, "load_aw_data_for_date_range", return_value={}
            ),
            patch.object(
                aw_analytics_export,
                "load_phone_hours_for_date_range",
                return_value={today: {10: {"Termux": 1800.0}}},
            ),
        ):
            reports = aw_analytics_export.generate_all_reports(lookback_days=1)

        daily = reports["daily"][0]
        self.assertEqual(today.isoformat(), daily["period"]["label"])
        self.assertEqual(0.5, daily["summary"]["total_active_time"]["hours"])
        self.assertEqual(0.5, daily["summary"]["dev_time"]["hours"])
        self.assertEqual("Termux", daily["dev_tools_breakdown"][0]["name"])


if __name__ == "__main__":
    unittest.main()
