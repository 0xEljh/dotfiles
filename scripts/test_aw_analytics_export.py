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

    def test_recent_development_and_planning_patterns_are_classified(self) -> None:
        def web_event(url: str, title: str, duration: float = 300.0) -> dict:
            return {
                "timestamp": "2026-07-02T10:00:00+08:00",
                "duration": duration,
                "data": {"url": url, "title": title},
            }

        aggregate = aw_analytics_export.aggregate_day_data(
            {
                "aw-watcher-web_test": [
                    web_event("https://github.com/org/repo/pull/1", "Pull request"),
                    web_event("https://huggingface.co/models", "Models"),
                    web_event("https://opencode.ai/docs", "OpenCode docs"),
                    web_event("https://z.ai/manage-apikey/apikey-list", "Z.ai API Platform"),
                    web_event("https://docs.z.ai/scenario-example/develop-tools/opencode", "OpenCode docs"),
                    web_event("https://wandb.ai/workspace/experiment/runs/run-id", "Experiment run"),
                    web_event("https://www.notion.so/workspace/page", "Notion"),
                    web_event("https://docs.google.com/document/d/1/edit", "Design doc"),
                    web_event("https://arxiv.org/abs/2607.12345", "Research paper"),
                    web_event("https://artificialanalysis.ai/models/model-name", "Model comparison"),
                    web_event("https://teams.microsoft.com/light-meetings/launch", "Call"),
                    web_event("https://app.macroscope.com/overview", "Overview"),
                ]
            }
        )

        self.assertEqual(1500.0, aggregate["totals"]["dev_time"])
        self.assertEqual(2100.0, aggregate["totals"]["planning_time"])
        self.assertEqual(300.0, aggregate["dev_tools"]["GitHub"])
        self.assertEqual(300.0, aggregate["dev_tools"]["Hugging Face"])
        self.assertEqual(300.0, aggregate["dev_tools"]["OpenCode"])
        self.assertEqual(300.0, aggregate["planning_apps"]["Z.ai API Platform"])
        self.assertNotIn("Z.ai API Platform", aggregate["dev_tools"])
        self.assertEqual(300.0, aggregate["dev_tools"]["Z.ai Docs"])
        self.assertEqual(300.0, aggregate["dev_tools"]["Weights & Biases"])
        self.assertEqual(300.0, aggregate["planning_apps"]["Notion"])
        self.assertEqual(300.0, aggregate["planning_apps"]["Google Docs"])
        self.assertEqual(300.0, aggregate["planning_apps"]["arXiv"])
        self.assertEqual(300.0, aggregate["planning_apps"]["AI Model Research"])
        self.assertEqual(300.0, aggregate["planning_apps"]["Microsoft Teams"])
        self.assertEqual(300.0, aggregate["planning_apps"]["Macroscope Planning"])
        self.assertNotIn("GitHub", aggregate["planning_apps"])

    def test_single_purpose_planning_sites_are_classified_by_domain(self) -> None:
        from aw_common import get_planning_site_name

        sites = {
            "https://app.macroscope.com/": "Macroscope Planning",
            "https://0xeljh.com/": "Expedition Log",
            "https://ma.to/": "Event Planning",
            "https://thinkingmachines.ai/": "Technical Reading",
        }

        for url, label in sites.items():
            with self.subTest(url=url):
                self.assertEqual(label, get_planning_site_name(url))

    def test_ambiguous_recent_sites_remain_unclassified(self) -> None:
        aggregate = aw_analytics_export.aggregate_day_data(
            {
                "aw-watcher-web_test": [
                    {
                        "timestamp": "2026-07-17T10:00:00+08:00",
                        "duration": 600.0,
                        "data": {
                            "url": "https://www.goodfire.ai/",
                            "title": "Goodfire",
                        },
                    },
                    {
                        "timestamp": "2026-07-17T10:10:00+08:00",
                        "duration": 600.0,
                        "data": {"url": "https://x.com/home", "title": "Home"},
                    },
                ]
            }
        )

        self.assertEqual(0, aggregate["totals"]["dev_time"])
        self.assertEqual(0, aggregate["totals"]["planning_time"])


class HostAliasTests(unittest.TestCase):
    def test_host_alias_is_explicit_not_a_generic_numeric_suffix_rule(self) -> None:
        from aw_common import extract_host_from_bucket

        self.assertEqual(
            "elijahs-macbook-air.local",
            extract_host_from_bucket(
                "aw-watcher-web-firefox_elijahs-macbook-air-2.tail82ff8b.ts.net"
            ),
        )
        self.assertEqual(
            "workstation-2.example.ts.net",
            extract_host_from_bucket(
                "aw-watcher-web-firefox_workstation-2.example.ts.net"
            ),
        )

    def test_macbook_browser_alias_uses_local_afk_periods(self) -> None:
        from aw_common import build_not_afk_periods_by_host, filter_events_by_afk

        periods = build_not_afk_periods_by_host(
            {
                "Elijahs-MacBook-Air.local": [
                    {
                        "timestamp": "2026-07-17T10:00:00+08:00",
                        "duration": 300.0,
                        "data": {"status": "not-afk"},
                    }
                ]
            }
        )
        filtered = filter_events_by_afk(
            [
                {
                    "_bucket": "aw-watcher-web-firefox_elijahs-macbook-air-2.tail82ff8b.ts.net",
                    "timestamp": "2026-07-17T10:04:00+08:00",
                    "duration": 300.0,
                    "data": {"url": "https://arxiv.org/abs/2607.12345"},
                }
            ],
            periods,
        )

        self.assertEqual(1, len(filtered))
        self.assertEqual(60.0, filtered[0]["duration"])


if __name__ == "__main__":
    unittest.main()
