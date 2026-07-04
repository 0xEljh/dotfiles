from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

import aw_notion_sync
import notion_day


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
            patch.object(aw_notion_sync, "fetch_sleep_summary", return_value=None),
            patch.object(aw_notion_sync, "fetch_phone_hours", return_value={}),
        ):
            result = aw_notion_sync.sync_date(date(2026, 6, 4), notion)

        # The page is ensured (and thus synced) even with no usable signals.
        self.assertTrue(result)
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
            patch.object(aw_notion_sync, "fetch_phone_hours", return_value={}),
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
            patch.object(aw_notion_sync, "fetch_phone_hours", return_value={}),
        ):
            result = aw_notion_sync.sync_date(date(2026, 6, 14), notion)

        # The bio tags still applied; the property failure was swallowed.
        self.assertTrue(result)
        self.assertTrue(any("00:00" in u for u in notion.updates))


class ContributorSeamTests(unittest.TestCase):
    """Phase 0: the day page is written by independent contributors, so sleep
    logs even on days with no ActivityWatch (desktop) data — the blind spot."""

    def test_sleep_logs_on_no_aw_day(self) -> None:
        notion = _RecordingNotion()
        fake_sleep = {
            "date": "2026-06-14",
            "sleep": {"duration_hours": 7.55, "duration_text": "7h33m"},
            "sleeping_hours": [0, 1, 2, 3, 4, 5, 6],
        }

        with (
            patch.object(
                aw_notion_sync, "load_aw_data_for_journal_day", return_value={}
            ),  # NO desktop activity at all
            patch.object(aw_notion_sync, "fetch_sleep_summary", return_value=fake_sleep),
            patch.object(aw_notion_sync, "fetch_phone_hours", return_value={}),
        ):
            result = aw_notion_sync.sync_date(date(2026, 6, 14), notion)

        self.assertTrue(result)  # page is synced despite zero AW data
        hourly_update = next(u for u in notion.updates if "00:00" in u)
        self.assertEqual({"select": {"name": "bio"}}, hourly_update["06:00"])
        prop_update = next(u for u in notion.updates if "Sleep Hours" in u)
        self.assertEqual({"number": 7.55}, prop_update["Sleep Hours"])


class PhoneMergeTests(unittest.TestCase):
    """Phone foreground time merges into the hourly activity model, so a
    phone-only hour (no desktop) still registers as active."""

    def test_phone_only_hour_is_active(self) -> None:
        stats = aw_notion_sync.compute_hourly_stats(
            {}, phone_hours={21: {"YouTube": 1800.0}}
        )

        self.assertIn(21, stats)
        self.assertGreaterEqual(stats[21]["active_time"], 1800)
        labels = [app for app, _ in stats[21]["top_apps"]]
        self.assertTrue(any("YouTube" in label for label in labels))

    def test_no_phone_no_extra_hours(self) -> None:
        # Without phone data the result is unchanged (no spurious hours).
        self.assertEqual({}, aw_notion_sync.compute_hourly_stats({}))


class DesktopFilteringWarningTests(unittest.TestCase):
    def test_warns_when_afk_filter_removes_all_desktop_activity(self) -> None:
        all_data = {
            "aw-watcher-window_host": [
                {
                    "timestamp": "2026-07-01T00:00:00+08:00",
                    "duration": 3600,
                    "data": {"app": "Zen", "title": "Release notes"},
                }
            ],
            "aw-watcher-afk_host": [
                {
                    "timestamp": "2026-07-01T00:00:00+08:00",
                    "duration": 3600,
                    "data": {"status": "afk"},
                }
            ],
        }

        with patch("builtins.print") as print_mock:
            stats = aw_notion_sync.compute_hourly_stats(
                all_data, phone_hours={10: {"YouTube": 1800}}
            )

        self.assertIn(10, stats)
        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list)
        self.assertIn("desktop ActivityWatch events", printed)
        self.assertIn("AFK", printed)


class PhoneClassificationTests(unittest.TestCase):
    """Phase 3a: phone dev/planning apps fold into the SAME Deep/Shallow-Work
    classification as desktop, reusing the aw_common taxonomy."""

    def _classify(self, phone_hours):
        stats = aw_notion_sync.compute_hourly_stats({}, phone_hours=phone_hours)
        return aw_notion_sync.determine_hourly_select_value(stats[10])

    def test_phone_coding_app_is_deep_work(self):
        self.assertEqual("Deep Work", self._classify({10: {"Termux": 3600}}))

    def test_phone_ai_app_is_shallow_work(self):
        # "Claude" normalises into the desktop AI_CHAT_APPS set → planning.
        self.assertEqual("Shallow Work", self._classify({10: {"Claude": 3600}}))

    def test_phone_planning_app_is_shallow_work(self):
        # "Notion" normalises into the desktop PLANNING_APPS set.
        self.assertEqual("Shallow Work", self._classify({10: {"Notion": 3600}}))

    def test_phone_comms_app_is_not_work(self):
        self.assertIsNone(self._classify({10: {"WhatsApp": 3600}}))

    def test_phone_app_category_reuses_desktop_taxonomy(self):
        import aw_common

        self.assertEqual("planning", aw_common.phone_app_category("Claude"))
        self.assertEqual("planning", aw_common.phone_app_category("Notion"))
        self.assertEqual("coding", aw_common.phone_app_category("Termux"))
        self.assertIsNone(aw_common.phone_app_category("WhatsApp"))


class _DictNotion:
    """Minimal Notion fake for notion_day.write_day_page: records pages.update
    property dicts and exposes a fixed set of existing page properties."""

    def __init__(self, existing=None):
        self.updates = []
        outer = self

        class _Pages:
            def retrieve(self, page_id):
                return {"properties": existing or {}}

            def update(self, page_id, properties):
                outer.updates.append(properties)
                return {}

        self.pages = _Pages()


def _ensure(notion, date_str):
    return "page-id"


def _noop_blocks(notion, page_id, blocks):
    return None


class WriteDayPageTests(unittest.TestCase):
    def _contrib(self, **kw):
        def c(date_str, existing):
            return notion_day.Contribution(**kw)

        return c

    def test_higher_priority_hour_tag_wins_regardless_of_order(self):
        notion = _DictNotion()
        bio = self._contrib(hour_tags={2: ("bio", notion_day.PRIORITY_BIO)})
        work = self._contrib(hour_tags={2: ("Deep Work", notion_day.PRIORITY_WORK)})

        # bio runs FIRST, work second — work must still win hour 2.
        notion_day.write_day_page(
            notion, "2026-06-14", [bio, work],
            ensure_page=_ensure, replace_blocks=_noop_blocks,
        )

        select = next(u for u in notion.updates if "02:00" in u)
        self.assertEqual({"select": {"name": "Deep Work"}}, select["02:00"])

    def test_existing_page_value_is_never_overwritten(self):
        notion = _DictNotion(existing={"02:00": {"select": {"name": "Meeting"}}})
        work = self._contrib(hour_tags={2: ("Deep Work", notion_day.PRIORITY_WORK)})

        notion_day.write_day_page(
            notion, "2026-06-14", [work],
            ensure_page=_ensure, replace_blocks=_noop_blocks,
        )

        self.assertEqual([], [u for u in notion.updates if "02:00" in u])

    def test_failing_contributor_does_not_block_others(self):
        notion = _DictNotion()

        def boom(date_str, existing):
            raise RuntimeError("kaboom")

        good = self._contrib(hour_tags={5: ("bio", notion_day.PRIORITY_BIO)})

        notion_day.write_day_page(
            notion, "2026-06-14", [boom, good],
            ensure_page=_ensure, replace_blocks=_noop_blocks,
        )

        select = next(u for u in notion.updates if "05:00" in u)
        self.assertEqual({"select": {"name": "bio"}}, select["05:00"])

    def test_missing_number_prop_does_not_sink_hour_tags(self):
        notion = _DictNotion()

        def flaky(page_id, properties):
            if "Sleep Hours" in properties:
                raise RuntimeError("Sleep Hours is not a property")
            notion.updates.append(properties)
            return {}

        notion.pages.update = flaky
        sleep = self._contrib(
            hour_tags={0: ("bio", notion_day.PRIORITY_BIO)},
            number_props={"Sleep Hours": 7.5},
        )

        notion_day.write_day_page(
            notion, "2026-06-14", [sleep],
            ensure_page=_ensure, replace_blocks=_noop_blocks,
        )

        self.assertTrue(any("00:00" in u for u in notion.updates))


if __name__ == "__main__":
    unittest.main()
