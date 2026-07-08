from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

import push_aw


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class PushAwBucketSelectionTests(unittest.TestCase):
    def test_hostname_match_handles_tailscale_numeric_suffix(self) -> None:
        self.assertTrue(
            push_aw.hostname_matches_current_machine(
                "Elijahs-MacBook-Air.local",
                "elijahs-macbook-air-2.tail82ff8b.ts.net",
            )
        )

    def test_hostname_match_does_not_treat_mac_as_macbook(self) -> None:
        self.assertFalse(
            push_aw.hostname_matches_current_machine(
                "Elijahs-MacBook-Air.local",
                "Mac",
            )
        )

    def test_selects_device_buckets_when_current_hostname_has_tailscale_suffix(
        self,
    ) -> None:
        buckets = {
            "aw-watcher-window_Elijahs-MacBook-Air.local": {
                "hostname": "Elijahs-MacBook-Air.local"
            },
            "aw-watcher-afk_Elijahs-MacBook-Air.local": {
                "hostname": "Elijahs-MacBook-Air.local"
            },
            "aw-watcher-window_Other-Mac.local": {"hostname": "Other-Mac.local"},
        }
        events_by_bucket = {
            "aw-watcher-window_Elijahs-MacBook-Air.local": [
                {
                    "timestamp": "2026-07-02T00:00:00+08:00",
                    "duration": 60,
                    "data": {"app": "Zen"},
                }
            ],
            "aw-watcher-afk_Elijahs-MacBook-Air.local": [
                {
                    "timestamp": "2026-07-02T00:00:00+08:00",
                    "duration": 60,
                    "data": {"status": "not-afk"},
                }
            ],
            "aw-watcher-window_Other-Mac.local": [
                {
                    "timestamp": "2026-07-02T00:00:00+08:00",
                    "duration": 60,
                    "data": {"app": "Safari"},
                }
            ],
        }

        def fake_get(url, params=None):
            if url.endswith("/buckets"):
                return _Response(buckets)
            bucket_id = url.rsplit("/", 2)[-2]
            return _Response(events_by_bucket[bucket_id])

        with (
            patch.object(
                push_aw.socket,
                "gethostname",
                return_value="elijahs-macbook-air-2.tail82ff8b.ts.net",
            ),
            patch.object(push_aw.requests, "get", side_effect=fake_get),
        ):
            data = push_aw.get_aw_data(date(2026, 7, 2))

        self.assertEqual(
            {
                "aw-watcher-window_Elijahs-MacBook-Air.local": events_by_bucket[
                    "aw-watcher-window_Elijahs-MacBook-Air.local"
                ],
                "aw-watcher-afk_Elijahs-MacBook-Air.local": events_by_bucket[
                    "aw-watcher-afk_Elijahs-MacBook-Air.local"
                ],
            },
            data,
        )

    def test_falls_back_to_non_empty_watcher_buckets_when_hostname_match_is_empty(
        self,
    ) -> None:
        buckets = {
            "aw-watcher-window_Mac": {"hostname": "Mac"},
            "aw-watcher-afk_Mac": {"hostname": "Mac"},
            "aw-watcher-window_Elijahs-MacBook-Air.local": {
                "hostname": "Elijahs-MacBook-Air.local"
            },
            "aw-watcher-afk_Elijahs-MacBook-Air.local": {
                "hostname": "Elijahs-MacBook-Air.local"
            },
        }
        events_by_bucket = {
            "aw-watcher-window_Mac": [],
            "aw-watcher-afk_Mac": [],
            "aw-watcher-window_Elijahs-MacBook-Air.local": [
                {
                    "timestamp": "2026-07-02T00:00:00+08:00",
                    "duration": 60,
                    "data": {"app": "Zen"},
                }
            ],
            "aw-watcher-afk_Elijahs-MacBook-Air.local": [
                {
                    "timestamp": "2026-07-02T00:00:00+08:00",
                    "duration": 60,
                    "data": {"status": "not-afk"},
                }
            ],
        }

        def fake_get(url, params=None):
            if url.endswith("/buckets"):
                return _Response(buckets)
            bucket_id = url.rsplit("/", 2)[-2]
            return _Response(events_by_bucket[bucket_id])

        with (
            patch.object(push_aw.socket, "gethostname", return_value="Mac"),
            patch.object(push_aw.requests, "get", side_effect=fake_get),
        ):
            data = push_aw.get_aw_data(date(2026, 7, 2))

        self.assertEqual(
            {
                "aw-watcher-window_Elijahs-MacBook-Air.local": events_by_bucket[
                    "aw-watcher-window_Elijahs-MacBook-Air.local"
                ],
                "aw-watcher-afk_Elijahs-MacBook-Air.local": events_by_bucket[
                    "aw-watcher-afk_Elijahs-MacBook-Air.local"
                ],
            },
            data,
        )


if __name__ == "__main__":
    unittest.main()
