"""Tests for paper_inbox_sync.py — Notion Paper Inbox → digital-garden bridge.

The bridge is deliberately mechanical (no prose generation): reactions are
copied verbatim, structure is added around them. These tests pin that contract
plus the idempotency guard that makes retries safe after a failed push.
Spec: digital-garden docs/design/paper-log-pipeline.md §5.2.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paper_inbox_sync as pis


def _row(**overrides):
    defaults = dict(
        page_id="page-1",
        title="Muon is Scalable for LLM Training",
        link="https://arxiv.org/abs/2502.16982",
        topic="optimizers beyond Adam",
        reaction="Claim: holds past 1B. Verdict after skim: deep read queued.",
        sketches=[],
    )
    defaults.update(overrides)
    return pis.PaperRow(**defaults)


class ParseArxivIdTests(unittest.TestCase):
    def test_abs_url(self) -> None:
        self.assertEqual("2502.16982", pis.parse_arxiv_id("https://arxiv.org/abs/2502.16982"))

    def test_pdf_url_with_version_strips_version(self) -> None:
        self.assertEqual("2502.16982", pis.parse_arxiv_id("https://arxiv.org/pdf/2502.16982v2"))

    def test_bare_id(self) -> None:
        self.assertEqual("2507.01234", pis.parse_arxiv_id("2507.01234"))

    def test_non_arxiv_url_is_none(self) -> None:
        self.assertIsNone(pis.parse_arxiv_id("https://example.com/paper"))

    def test_none_is_none(self) -> None:
        self.assertIsNone(pis.parse_arxiv_id(None))


class SlugifyTests(unittest.TestCase):
    def test_spaces_and_case(self) -> None:
        self.assertEqual("optimizers-beyond-adam", pis.slugify("Optimizers beyond Adam"))

    def test_punctuation_collapses(self) -> None:
        self.assertEqual("rl-scaling-envs", pis.slugify("RL: scaling & envs!"))


class TopicNoteTemplateTests(unittest.TestCase):
    def test_frontmatter_fields(self) -> None:
        note = pis.topic_note_template("optimizers beyond Adam", "2026-07-04")
        self.assertIn('title: "optimizers beyond Adam"', note)
        # A missing date would be re-defaulted to the build day on EVERY
        # build (lib/utils/posts.ts buildMetadata), silently shifting the
        # logged date — the template must pin it.
        self.assertIn('date: "2026-07-04"', note)
        self.assertIn('categories: ["paper log"]', note)
        self.assertIn("stage: sighted", note)


class BuildPaperBlockTests(unittest.TestCase):
    def test_arxiv_block_has_preview_and_verbatim_reaction(self) -> None:
        row = _row()
        block = pis.build_paper_block(row, "2502.16982", ["/field-sketches/optimizers-beyond-adam/2502.16982-1.png"])
        self.assertIn("## Muon is Scalable for LLM Training", block)
        self.assertIn('<PaperPreview arxivId="2502.16982" />', block)
        self.assertIn(row.reaction, block)  # verbatim, never rewritten
        self.assertIn("![field sketch](/field-sketches/optimizers-beyond-adam/2502.16982-1.png)", block)

    def test_non_arxiv_link_gets_source_line_not_preview(self) -> None:
        row = _row(link="https://example.com/paper")
        block = pis.build_paper_block(row, None, [])
        self.assertNotIn("PaperPreview", block)
        self.assertIn("[source](https://example.com/paper)", block)

    def test_no_link_no_reaction_still_yields_heading(self) -> None:
        row = _row(link=None, reaction="")
        block = pis.build_paper_block(row, None, [])
        self.assertIn("## Muon is Scalable for LLM Training", block)


class AlreadyLandedTests(unittest.TestCase):
    def test_arxiv_id_match(self) -> None:
        self.assertTrue(pis.already_landed('x <PaperPreview arxivId="2502.16982" /> y', _row(), "2502.16982"))

    def test_link_match(self) -> None:
        row = _row(link="https://example.com/p")
        self.assertTrue(pis.already_landed("see [source](https://example.com/p)", row, None))

    def test_heading_match(self) -> None:
        self.assertTrue(pis.already_landed("## Muon is Scalable for LLM Training\n", _row(link=None), None))

    def test_fresh_content_is_not_landed(self) -> None:
        self.assertFalse(pis.already_landed("## Some other paper\n", _row(), "2502.16982"))


class ExtractRowTests(unittest.TestCase):
    def test_full_page(self) -> None:
        page = {
            "id": "page-9",
            "properties": {
                "Title": {"title": [{"plain_text": "SOAP"}]},
                "Link": {"url": "https://arxiv.org/abs/2409.11321"},
                "Topic": {"select": {"name": "optimizers beyond Adam"}},
                "Reaction": {"rich_text": [{"plain_text": "Shampoo "}, {"plain_text": "variant."}]},
                "Sketch": {"files": [
                    {"name": "page1.png", "type": "file", "file": {"url": "https://s3/expiring"}},
                ]},
                "Status": {"select": None},
            },
        }
        row = pis.extract_row(page)
        self.assertEqual("page-9", row.page_id)
        self.assertEqual("SOAP", row.title)
        self.assertEqual("https://arxiv.org/abs/2409.11321", row.link)
        self.assertEqual("optimizers beyond Adam", row.topic)
        self.assertEqual("Shampoo variant.", row.reaction)
        self.assertEqual([("page1.png", "https://s3/expiring")], row.sketches)

    def test_empty_topic_falls_back_to_misc(self) -> None:
        page = {"id": "p", "properties": {"Title": {"title": []}, "Topic": {"select": None}}}
        row = pis.extract_row(page)
        self.assertEqual(pis.DEFAULT_TOPIC, row.topic)
        self.assertEqual("untitled sighting", row.title)


class QueryPendingTests(unittest.TestCase):
    def test_paginates_and_filters_out_landed(self) -> None:
        class _FakeDataSources:
            def __init__(self) -> None:
                self.queries = []

            def query(self, **kwargs):
                self.queries.append(kwargs)
                if kwargs.get("start_cursor") is None:
                    return {
                        "results": [{"id": "a", "properties": {}}],
                        "has_more": True,
                        "next_cursor": "c2",
                    }
                return {"results": [{"id": "b", "properties": {}}], "has_more": False}

        class _FakeNotion:
            def __init__(self) -> None:
                self.data_sources = _FakeDataSources()

        notion = _FakeNotion()
        pages = pis.query_pending(notion, "ds-1")
        self.assertEqual(["a", "b"], [p["id"] for p in pages])
        self.assertEqual(2, len(notion.data_sources.queries))
        # Filter must catch rows where Status was never set, not just "inbox".
        self.assertIn("or", notion.data_sources.queries[0]["filter"])


class MarkLandedTests(unittest.TestCase):
    def test_sets_status_and_landed_url(self) -> None:
        class _FakePages:
            def __init__(self) -> None:
                self.updates = []

            def update(self, **kwargs):
                self.updates.append(kwargs)
                return {}

        class _FakeNotion:
            def __init__(self) -> None:
                self.pages = _FakePages()

        notion = _FakeNotion()
        pis.mark_landed(notion, "page-1", "https://0xeljh.com/posts/optimizers-beyond-adam")
        (update,) = notion.pages.updates
        self.assertEqual("page-1", update["page_id"])
        self.assertEqual(
            {"select": {"name": "landed"}}, update["properties"][pis.PROP_STATUS]
        )
        self.assertEqual(
            {"url": "https://0xeljh.com/posts/optimizers-beyond-adam"},
            update["properties"][pis.PROP_LANDED_URL],
        )


class LandRowTests(unittest.TestCase):
    def _repo(self) -> Path:
        repo = Path(self._tmp.name)
        (repo / "content" / "posts").mkdir(parents=True)
        return repo

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_creates_topic_note_and_appends_block(self) -> None:
        repo = self._repo()
        row = _row()
        with patch.object(pis, "download_file") as dl:
            landed = pis.land_row(row, repo, "https://0xeljh.com", "2026-07-04")
        self.assertEqual("https://0xeljh.com/posts/optimizers-beyond-adam", landed)
        note = (repo / "content" / "posts" / "optimizers-beyond-adam.mdx").read_text()
        self.assertIn('title: "optimizers beyond Adam"', note)
        self.assertIn('<PaperPreview arxivId="2502.16982" />', note)
        self.assertIn(row.reaction, note)
        dl.assert_not_called()

    def test_second_landing_is_skipped_and_file_unchanged(self) -> None:
        repo = self._repo()
        row = _row()
        with patch.object(pis, "download_file"):
            pis.land_row(row, repo, "https://0xeljh.com", "2026-07-04")
            before = (repo / "content" / "posts" / "optimizers-beyond-adam.mdx").read_text()
            landed = pis.land_row(row, repo, "https://0xeljh.com", "2026-07-04")
        self.assertIsNone(landed)
        after = (repo / "content" / "posts" / "optimizers-beyond-adam.mdx").read_text()
        self.assertEqual(before, after)

    def test_sketches_download_into_public_field_sketches(self) -> None:
        repo = self._repo()
        row = _row(sketches=[("GoodNotes p1.png", "https://s3/expiring-1")])
        saved = []

        def fake_download(url: str, dest: Path) -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"png")
            saved.append((url, dest))

        with patch.object(pis, "download_file", side_effect=fake_download):
            pis.land_row(row, repo, "https://0xeljh.com", "2026-07-04")

        ((url, dest),) = saved
        self.assertEqual("https://s3/expiring-1", url)
        self.assertEqual(repo / "public" / "field-sketches" / "optimizers-beyond-adam" / "2502.16982-1.png", dest)
        note = (repo / "content" / "posts" / "optimizers-beyond-adam.mdx").read_text()
        self.assertIn("![field sketch](/field-sketches/optimizers-beyond-adam/2502.16982-1.png)", note)


if __name__ == "__main__":
    unittest.main()
