import unittest
from itertools import product

from aw_common import get_browser_dev_tool_name
from aw_notion_sync import compute_hourly_stats, determine_hourly_select_value
from aw_analytics_export import aggregate_day_data


class T3ActivityTests(unittest.TestCase):
    def test_t3_https_counts_as_development(self):
        for host, port in product(("sleeper-service", "contents-may-differ"), ("", ":443")):
            with self.subTest(host=host, port=port):
                url = f"https://{host}.inanga-kardashev.ts.net{port}/session/123"
                self.assertEqual("T3 Code", get_browser_dev_tool_name(url))
                event = {
                    "timestamp": "2026-07-02T10:00:00+08:00",
                    "duration": 3600.0,
                }
                data = {
                    "aw-watcher-web_test": [dict(event, data={"url": url})],
                    "aw-watcher-afk_test": [dict(event, data={"status": "not-afk"})],
                    "aw-watcher-window_test": [dict(event, data={"app": "firefox"})],
                }
                aggregate = aggregate_day_data(data)
                self.assertEqual(3600.0, aggregate["dev_tools"]["T3 Code"])
                self.assertEqual(3600.0, aggregate["totals"]["dev_time"])
                stats = compute_hourly_stats(data)[10]
                self.assertEqual("Deep Work", determine_hourly_select_value(stats))

    def test_other_services_are_not_t3(self):
        for url in (
            "https://sleeper-service.inanga-kardashev.ts.net:8779/",
            "https://sleeper-service.inanga-kardashev.ts.net:8443/",
            "https://contents-may-differ.inanga-kardashev.ts.net:8443/",
            "https://contents-may-differ.inanga-kardashev.ts.net:3000/",
            "https://contents-may-differ.inanga-kardashev.ts.net:8779/",
            "http://contents-may-differ.inanga-kardashev.ts.net/",
            "http://sleeper-service.inanga-kardashev.ts.net/",
            "https://other.inanga-kardashev.ts.net/",
            "https://sleeper-service.inanga-kardashev.ts.net.example.com/",
        ):
            with self.subTest(url=url):
                self.assertIsNone(get_browser_dev_tool_name(url, "T3 Code"))
