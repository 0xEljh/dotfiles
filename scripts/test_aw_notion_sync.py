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


class _RecordingPages:
    def __init__(self, updates):
        self._updates = updates

    def retrieve(self, page_id):
        return {"properties": {}}  # all hourly properties empty → fillable

    def update(self, page_id, properties):
        self._updates.append(properties)
        return {}


class _RecordingChildren:
    def __init__(self, appended):
        self._appended = appended

    def list(self, block_id):
        return {"results": []}

    def append(self, block_id, children):
        self._appended.append(children)
        return {}


class _RecordingBlocks:
    def __init__(self, appended):
        self.children = _RecordingChildren(appended)

    def delete(self, block_id):
        return {}


class _RecordingNotion:
    def __init__(self):
        self.updates = []
        self.appended = []
        self.data_sources = _FakeDataSources()
        self.data_sources.query = lambda **kw: {"results": [{"id": "page-1"}]}
        self.pages = _RecordingPages(self.updates)
        self.blocks = _RecordingBlocks(self.appended)


class SleepEnrichmentTests(unittest.TestCase):
    def test_tags_bio_hours_and_writes_sleep_property(self) -> None:
        notion = _RecordingNotion()
        fake_sleep = {
            "date": "2026-06-14",
            "sleep": {"duration_hours": 7.55, "duration_text": "7h33m"},
            "sleeping_hours": [0, 1, 2, 3, 4, 5, 6],
        }

        with (
            patch.object(
                aw_notion_sync,
                "load_aw_data_for_journal_day",
                return_value={"aw-watcher-window_host": [{"id": 1}]},
            ),
            patch.object(
                aw_notion_sync, "compute_hourly_stats", return_value={9: {"active_time": 0}}
            ),
            patch.object(aw_notion_sync, "build_notion_blocks", return_value=[]),
            patch.object(aw_notion_sync, "fetch_sleep_summary", return_value=fake_sleep),
        ):
            result = aw_notion_sync.sync_date(date(2026, 6, 14), notion)

        self.assertTrue(result)

        hourly_update = next(u for u in notion.updates if "00:00" in u)
        self.assertEqual({"select": {"name": "bio"}}, hourly_update["00:00"])
        self.assertEqual({"select": {"name": "bio"}}, hourly_update["06:00"])
        self.assertNotIn("07:00", hourly_update)  # only the supplied hours

        prop_update = next(u for u in notion.updates if "Sleep Hours" in u)
        self.assertEqual({"number": 7.55}, prop_update["Sleep Hours"])

    def test_missing_sleep_property_does_not_break_sync(self) -> None:
        notion = _RecordingNotion()

        def flaky_update(page_id, properties):
            if "Sleep Hours" in properties:
                raise RuntimeError("Sleep Hours is not a property that exists")
            notion.updates.append(properties)
            return {}

        notion.pages.update = flaky_update
        fake_sleep = {
            "date": "2026-06-14",
            "sleep": {"duration_hours": 7.55, "duration_text": "7h33m"},
            "sleeping_hours": [0, 1],
        }

        with (
            patch.object(
                aw_notion_sync,
                "load_aw_data_for_journal_day",
                return_value={"aw-watcher-window_host": [{"id": 1}]},
            ),
            patch.object(
                aw_notion_sync, "compute_hourly_stats", return_value={9: {"active_time": 0}}
            ),
            patch.object(aw_notion_sync, "build_notion_blocks", return_value=[]),
            patch.object(aw_notion_sync, "fetch_sleep_summary", return_value=fake_sleep),
        ):
            result = aw_notion_sync.sync_date(date(2026, 6, 14), notion)

        # The bio tags still applied; the property failure was swallowed.
        self.assertTrue(result)
        self.assertTrue(any("00:00" in u for u in notion.updates))


if __name__ == "__main__":
    unittest.main()
