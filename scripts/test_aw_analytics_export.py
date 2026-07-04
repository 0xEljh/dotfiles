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

    def test_generate_reports_warns_when_desktop_data_is_stale(self) -> None:
        today = date(2026, 6, 20)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.combine(today, time(12, 0), tzinfo=tz)

        with (
            patch.object(aw_analytics_export, "datetime", FixedDateTime),
            patch.object(
                aw_analytics_export,
                "load_aw_data_for_date_range",
                return_value={date(2026, 6, 18): {}},
            ),
            patch.object(
                aw_analytics_export,
                "load_phone_hours_for_date_range",
                return_value={today: {10: {"Termux": 1800.0}}},
            ),
        ):
            reports = aw_analytics_export.generate_all_reports(lookback_days=3)

        quality = reports["data_quality"]
        self.assertEqual("2026-06-18", quality["latest_desktop_date"])
        self.assertEqual("2026-06-20", quality["latest_phone_date"])
        self.assertEqual(2, quality["desktop_days_behind_today"])
        self.assertTrue(quality["warnings"])


class ActivityTaxonomyTests(unittest.TestCase):
    def test_devin_counts_as_dev_tool_while_claude_counts_as_ai_planning(self) -> None:
        aggregate = aw_analytics_export.aggregate_day_data(
            {
                "aw-watcher-window_test": [
                    {
                        "timestamp": "2026-07-02T10:00:00+08:00",
                        "duration": 1200.0,
                        "data": {"app": "Devin", "title": "Devin"},
                    }
                ],
                "aw-watcher-web_test": [
                    {
                        "timestamp": "2026-07-02T10:20:00+08:00",
                        "duration": 600.0,
                        "data": {
                            "url": "https://claude.com/chat/abc",
                            "title": "Claude",
                        },
                    },
                    {
                        "timestamp": "2026-07-02T10:30:00+08:00",
                        "duration": 300.0,
                        "data": {
                            "url": "https://devin.ai/sessions/abc",
                            "title": "Devin",
                        },
                    },
                ],
            }
        )

        self.assertEqual(1200.0, aggregate["totals"]["active_time"])
        self.assertEqual(1500.0, aggregate["totals"]["dev_time"])
        self.assertEqual(600.0, aggregate["totals"]["planning_time"])
        self.assertEqual(600.0, aggregate["totals"]["ai_chat_time"])
        self.assertEqual(1500.0, aggregate["dev_tools"]["Devin"])
        self.assertNotIn("Devin", aggregate["ai_chats"])
        self.assertEqual(600.0, aggregate["ai_chats"]["Claude"])

    def test_reviewed_ambiguous_taxonomy_updates(self) -> None:
        aggregate = aw_analytics_export.aggregate_day_data(
            {
                "aw-watcher-window_test": [
                    {
                        "timestamp": "2026-07-02T10:00:00+08:00",
                        "duration": 3600.0,
                        "data": {"app": "ForzaHorizon6", "title": "Forza Horizon 6"},
                    }
                ],
                "aw-watcher-web_test": [
                    {
                        "timestamp": "2026-07-02T10:00:00+08:00",
                        "duration": 1800.0,
                        "data": {"url": "http://localhost:19000/", "title": "app.py"},
                    },
                    {
                        "timestamp": "2026-07-02T10:30:00+08:00",
                        "duration": 1200.0,
                        "data": {
                            "url": "https://drive.google.com/file/d/example",
                            "title": "project.pdf - Google Drive",
                        },
                    },
                    {
                        "timestamp": "2026-07-02T10:50:00+08:00",
                        "duration": 900.0,
                        "data": {
                            "url": "https://mail.google.com/mail/u/0/#inbox",
                            "title": "Inbox - Gmail",
                        },
                    },
                    {
                        "timestamp": "2026-07-02T11:00:00+08:00",
                        "duration": 300.0,
                        "data": {
                            "url": "https://meet.google.com/abc-defg-hij",
                            "title": "Google Meet",
                        },
                    },
                    {
                        "timestamp": "2026-07-02T11:05:00+08:00",
                        "duration": 600.0,
                        "data": {
                            "url": "https://magic-wormhole.readthedocs.io/en/latest/",
                            "title": "Magic-Wormhole documentation",
                        },
                    },
                ],
            }
        )

        self.assertEqual(0.0, aggregate["totals"]["active_time"])
        self.assertEqual(1800.0, aggregate["totals"]["dev_time"])
        self.assertEqual(3000.0, aggregate["totals"]["planning_time"])
        self.assertEqual(1800.0, aggregate["dev_tools"]["Localhost"])
        self.assertEqual(1200.0, aggregate["planning_apps"]["Google Drive"])
        self.assertEqual(900.0, aggregate["planning_apps"]["Gmail"])
        self.assertEqual(300.0, aggregate["planning_apps"]["Google Meet"])
        self.assertEqual(600.0, aggregate["planning_apps"]["Documentation"])
        self.assertNotIn("ForzaHorizon6", aggregate["top_apps"])


if __name__ == "__main__":
    unittest.main()
