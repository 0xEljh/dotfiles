from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

import aw_notion_sync


class _FakeDataSources:
    def __init__(self) -> None:
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"results": []}


class _FakePages:
    def __init__(self) -> None:
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "created-page-id"}


class _FakeNotion:
    def __init__(self) -> None:
        self.data_sources = _FakeDataSources()
        self.pages = _FakePages()


class SyncDateTests(unittest.TestCase):
    def test_creates_page_even_when_hourly_stats_are_empty(self) -> None:
        notion = _FakeNotion()

        with (
            patch.object(
                aw_notion_sync,
                "load_aw_data_for_journal_day",
                return_value={"aw-watcher-window_host": [{"id": 1}]},
            ),
            patch.object(aw_notion_sync, "compute_hourly_stats", return_value={}),
        ):
            result = aw_notion_sync.sync_date(date(2026, 6, 4), notion)

        self.assertFalse(result)
        self.assertEqual(1, len(notion.pages.created))
        # Key by the stable property ID "title", not the display name: the
        # Time Accounting title column was renamed (currently ""), which broke
        # page creation when this was keyed by "Name".
        self.assertEqual(
            {
                "title": {"title": [{"text": {"content": "2026-06-04"}}]},
                "Date": {"date": {"start": "2026-06-04"}},
            },
            notion.pages.created[0]["properties"],
        )


if __name__ == "__main__":
    unittest.main()
