"""Consumer-level checks for notion-cat planning and publication."""

import copy
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import notion_cat as cat
from notion_client import RetryOptions
from notion_client.errors import HTTPResponseError, RequestTimeoutError


class RecordingClient:
    def __init__(self):
        self.events = []
        self.limit = 20 * 1024 * 1024
        self.failure = None
        self.upload_status = "uploaded"
        self.expiry = "2999-01-01T00:00:00Z"
        self.users = SimpleNamespace(me=self.me)
        self.file_uploads = SimpleNamespace(create=self.create_upload, send=self.send)
        self.pages = SimpleNamespace(create=self.create_page)
        self.blocks = SimpleNamespace(children=SimpleNamespace(append=self.append))

    def record(self, stage, payload=None):
        self.events.append((stage, copy.deepcopy(payload)))
        if self.failure == stage:
            raise RuntimeError("unsafe remote response with a signed URL")

    def me(self):
        self.record("limit")
        return {
            "bot": {"workspace_limits": {"max_file_upload_size_in_bytes": self.limit}}
        }

    def create_upload(self, **kwargs):
        self.record("create_upload", kwargs)
        number = sum(stage == "create_upload" for stage, _ in self.events)
        return {
            "id": f"upload-{number}",
            "status": "pending",
            "expiry_time": self.expiry,
        }

    def send(self, file_upload_id, file):
        name, stream, mime = file
        self.record(
            "send",
            {"id": file_upload_id, "name": name, "bytes": stream.read(), "mime": mime},
        )
        return {
            "id": file_upload_id,
            "status": self.upload_status,
            "expiry_time": self.expiry,
        }

    def create_page(self, **kwargs):
        self.record("page", kwargs)
        return {"id": "page-id", "url": "https://www.notion.so/pageid"}

    def append(self, **kwargs):
        self.record("append", kwargs)
        return {
            "results": [
                {"id": f"block-{len(self.events)}-{i}"}
                for i, _ in enumerate(kwargs["children"])
            ]
        }

    def close(self):
        pass


class NotionCatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = RecordingClient()

    def asset(self, name="shot.png", data=b"generated fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def doc(self, text, name="doc.md"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return str(path)

    def run_cli(self, *args, credentials=True, stdin=b""):
        env = (
            {"NOTION_TOKEN": "test", "NOTION_CAT_DATA_SOURCE_ID": "destination"}
            if credentials
            else {}
        )
        out, err = io.TextIOWrapper(io.BytesIO()), io.StringIO()
        input_stream = io.TextIOWrapper(io.BytesIO(stdin))
        factory = Mock(return_value=self.client)
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(
                sys, "argv", ["notion-cat", "--suppress-output", *map(str, args)]
            ),
            patch.object(sys, "stdout", out),
            patch.object(sys, "stderr", err),
            patch.object(sys, "stdin", input_stream),
            patch.object(cat, "load_env_files", return_value=None),
            patch.object(cat, "Client", factory),
            patch.object(cat, "UploadClient", factory),
        ):
            result = cat.main()
            out.flush()
            stdout = out.buffer.getvalue().decode()
        return result, stdout, err.getvalue(), factory

    def appended(self):
        return [
            block
            for stage, payload in self.client.events
            if stage == "append"
            for block in payload["children"]
        ]

    def test_upload_order_caption_and_symlink_reuse(self):
        asset = self.asset()
        (self.root / "alias.png").symlink_to(asset)
        doc = self.doc(
            'before\n\n![**First** *caption*](shot.png "title")\n\nafter\n\n![Second](alias.png)'
        )
        result, _, err, _ = self.run_cli(doc)
        self.assertEqual(result, 0, err)
        self.assertEqual(
            [stage for stage, _ in self.client.events],
            ["limit", "create_upload", "send", "page", "append"],
        )
        blocks = self.appended()
        self.assertEqual(
            [b["type"] for b in blocks], ["paragraph", "image", "paragraph", "image"]
        )
        images = [b["image"] for b in blocks if b["type"] == "image"]
        self.assertEqual(
            [i["file_upload"]["id"] for i in images], ["upload-1", "upload-1"]
        )
        self.assertEqual(
            ["".join(t["text"]["content"] for t in i["caption"]) for i in images],
            ["First caption", "Second"],
        )

    def test_each_document_has_its_own_directory_and_parse(self):
        self.asset("a/shot.png", b"a")
        self.asset("b/shot.png", b"b")
        docs = [
            self.doc("![local](shot.png)", f"{folder}/doc.md") for folder in ["a", "b"]
        ]
        result, _, err, _ = self.run_cli(*docs)
        self.assertEqual(result, 0, err)
        self.assertEqual(
            [p["bytes"] for s, p in self.client.events if s == "send"], [b"a", b"b"]
        )
        self.assertEqual([b["type"] for b in self.appended()], ["image", "image"])

    def test_html_fence_uploads_exact_bytes_as_embed_in_order(self):
        content = b"<!doctype html><button>Play</button><script>let n = 0;</script>\n"
        self.asset("viewer.html", content)
        doc = self.doc("before\n\n```notion-html\n./viewer.html\n```\n\nafter")
        result, _, err, _ = self.run_cli(doc)
        self.assertEqual(result, 0, err)
        self.assertEqual(
            [stage for stage, _ in self.client.events],
            ["limit", "create_upload", "send", "page", "append"],
        )
        blocks = self.appended()
        self.assertEqual(
            [block["type"] for block in blocks], ["paragraph", "embed", "paragraph"]
        )
        self.assertEqual(
            blocks[1]["embed"],
            {"type": "file_upload", "file_upload": {"id": "upload-1"}},
        )
        sent = next(payload for stage, payload in self.client.events if stage == "send")
        self.assertEqual(sent["bytes"], content)
        self.assertEqual(sent["mime"], "text/html")

    def test_html_paths_are_literal_and_source_relative(self):
        name = "viewer 100%20 #?.htm"
        documents = []
        for folder in ["a", "b"]:
            self.asset(f"{folder}/{name}", folder.encode())
            documents.append(
                self.doc(f"```notion-html\n{name}\n```", f"{folder}/doc.md")
            )
        result, _, err, _ = self.run_cli(*documents)
        self.assertEqual(result, 0, err)
        self.assertEqual(
            [(p["name"], p["bytes"]) for s, p in self.client.events if s == "send"],
            [(name, b"a"), (name, b"b")],
        )
        self.assertEqual(
            [b["embed"]["file_upload"]["id"] for b in self.appended()],
            ["upload-1", "upload-2"],
        )

    def test_html_and_image_modes_are_independent_and_reuse_uploads(self):
        html = self.asset("viewer.html", b"<button>Play</button>")
        (self.root / "alias.html").symlink_to(html)
        self.asset()
        doc = self.doc(
            "![shot](shot.png)\n\n```notion-html\nviewer.html\n```\n\n"
            "```notion-html\nalias.html\n```"
        )
        for flags, types, mimes in [
            ([], ["image", "embed", "embed"], ["image/png", "text/html"]),
            (["--images=alt"], ["paragraph", "embed", "embed"], ["text/html"]),
            (["--html=literal"], ["image", "code", "code"], ["image/png"]),
        ]:
            with self.subTest(flags=flags):
                self.client.events.clear()
                result, _, err, _ = self.run_cli(*flags, doc)
                self.assertEqual(result, 0, err)
                blocks = self.appended()
                self.assertEqual([b["type"] for b in blocks], types)
                self.assertEqual(
                    [p["mime"] for s, p in self.client.events if s == "send"], mimes
                )
                if "embed" in types:
                    expected_id = "upload-1" if flags else "upload-2"
                    self.assertEqual(
                        [b["embed"]["file_upload"]["id"] for b in blocks[1:]],
                        [expected_id, expected_id],
                    )

    def test_html_only_containers_preserve_nested_placement(self):
        self.asset("viewer.html")
        for text, container in [
            ("- ```notion-html\n  viewer.html\n  ```", "bulleted_list_item"),
            ("> ```notion-html\n> viewer.html\n> ```", "quote"),
        ]:
            with self.subTest(container=container):
                self.client.events.clear()
                result, _, err, _ = self.run_cli("--images=alt", self.doc(text))
                self.assertEqual(result, 0, err)
                appends = [p for s, p in self.client.events if s == "append"]
                self.assertEqual(
                    [b["type"] for b in appends[0]["children"]], [container]
                )
                self.assertEqual([b["type"] for b in appends[1]["children"]], ["embed"])

    def test_code_and_html_preserve_list_item_order(self):
        self.asset("viewer.html")
        doc = self.doc(
            '- Example\n\n  ```python\n  print("before")\n  ```\n\n'
            "  ```notion-html\n  viewer.html\n  ```\n\n  after"
        )
        result, _, err, _ = self.run_cli(doc)
        self.assertEqual(result, 0, err)
        appends = [
            payload for stage, payload in self.client.events if stage == "append"
        ]
        self.assertEqual(
            [b["type"] for b in appends[0]["children"]], ["bulleted_list_item"]
        )
        self.assertEqual(
            [b["type"] for b in appends[1]["children"]],
            ["code", "embed", "paragraph"],
        )

    def test_list_text_overflow_stays_before_html(self):
        self.asset("viewer.html")
        doc = self.doc(
            "- " + "**text** " * 60 + "\n\n  ```notion-html\n  viewer.html\n  ```"
        )
        result, _, err, _ = self.run_cli(doc)
        self.assertEqual(result, 0, err)
        appends = [
            payload for stage, payload in self.client.events if stage == "append"
        ]
        self.assertEqual(
            [block["type"] for block in appends[1]["children"]], ["paragraph", "embed"]
        )

    def test_invalid_html_references_fail_before_client_construction(self):
        self.asset("not-html.txt")
        for body in [
            "",
            "one.html\ntwo.html",
            "missing.html",
            "not-html.txt",
            "https://example.com/a.html",
        ]:
            with self.subTest(body=body):
                result, _, err, factory = self.run_cli(
                    self.doc(f"```notion-html\n{body}\n```"), credentials=False
                )
                self.assertEqual(result, 2, err)
                self.assertIn("planning", err)
                self.assertIn("--html=literal", err)
                factory.assert_not_called()

    def test_html_literal_and_code_examples_never_upload_missing_files(self):
        marker = "```notion-html\nmissing.html\n```"
        for flags, text in [
            (["--html=literal"], marker),
            (["--raw"], marker),
            ([], "````markdown\n" + marker + "\n````"),
            ([], "```html\n<button>Play</button>\n```"),
            ([], "```notion-html example\nmissing.html\n```"),
            ([], "```notion\\-html\nmissing.html\n```"),
            ([], "```notion&#45;html\nmissing.html\n```"),
            ([], "[Open viewer](missing.html)\n\n<iframe src='missing.html'></iframe>"),
        ]:
            with self.subTest(flags=flags, text=text):
                self.client.events.clear()
                result, _, err, _ = self.run_cli(*flags, self.doc(text))
                self.assertEqual(result, 0, err)
                self.assertEqual([s for s, _ in self.client.events], ["page", "append"])

    def test_html_unicode_controls_fail_before_client_construction(self):
        for char in ["\u0080", "\u009b"]:
            with self.subTest(char=repr(char)):
                name = f"viewer{char}.html"
                self.asset(name)
                result, _, err, factory = self.run_cli(
                    "--dry-run",
                    self.doc(f"```notion-html\n{name}\n```"),
                    credentials=False,
                )
                self.assertEqual(result, 2, err)
                self.assertIn("control character", err)
                factory.assert_not_called()

    def test_html_dry_run_reports_attachment_type_without_credentials(self):
        asset = self.asset("viewer.html", b"<p>viewer</p>")
        doc = self.doc(
            "```notion-html\nviewer.html\n```\n\n```notion-html\nviewer.html\n```"
        )
        result, _, err, factory = self.run_cli("--dry-run", doc, credentials=False)
        self.assertEqual(result, 0, err)
        factory.assert_not_called()
        for expected in [
            str(asset),
            "text/html",
            "type=embed",
            "action=reuse",
            "No upload",
        ]:
            self.assertIn(expected, err)

    def test_html_send_failure_stops_before_page_and_reports_upload(self):
        self.asset("viewer.html")
        self.client.failure = "send"
        result, _, err, _ = self.run_cli(self.doc("```notion-html\nviewer.html\n```"))
        self.assertEqual(result, 1, err)
        self.assertEqual(
            [s for s, _ in self.client.events], ["limit", "create_upload", "send"]
        )
        self.assertIn("binary send", err)
        self.assertIn("upload-1", err)
        self.assertNotIn("unsafe remote response", err)

    def test_images_keep_container_order_including_first_and_nested(self):
        self.asset()
        for prefix, container in [("", "bulleted_list_item"), ("> ", "quote")]:
            with self.subTest(container=container):
                self.client.events.clear()
                text = "- ![first](shot.png)\n\n  middle\n\n  ![last](shot.png)\n\n  - ![nested](shot.png)"
                if prefix:
                    text = "> ![first](shot.png)\n>\n> middle\n>\n> ![last](shot.png)\n>\n> - ![nested](shot.png)"
                result, _, err, _ = self.run_cli(self.doc(text))
                self.assertEqual(result, 0, err)
                appends = [p for s, p in self.client.events if s == "append"]
                self.assertEqual(
                    [b["type"] for b in appends[0]["children"]], [container]
                )
                self.assertEqual(
                    [b["type"] for b in appends[1]["children"]],
                    ["image", "paragraph", "image", "bulleted_list_item"],
                )
                self.assertEqual([b["type"] for b in appends[2]["children"]], ["image"])

    def test_unsupported_placements_and_sources_fail_without_mutation(self):
        for text in [
            "text ![alt](shot.png)",
            "# ![alt](shot.png)",
            "| column |\n| --- |\n| ![alt](shot.png) |",
            "![alt](https://example.com/shot.png)",
            "![alt](shot.png?query)",
            "![alt](shot.png#fragment)",
            "![alt](file:/shot.png)",
            "[![alt](shot.png)](https://example.com)",
        ]:
            with self.subTest(text=text):
                result, _, err, factory = self.run_cli(self.doc(text))
                self.assertEqual(result, 2, err)
                self.assertIn("planning", err)
                self.assertIn("--images=alt", err)
                factory.assert_not_called()

    def test_asset_validation(self):
        for filename, expected in [
            ("missing.png", "missing"),
            ("shot.txt", "extension"),
            ("folder.png", "regular file"),
            ("large.png", "20971520"),
            ("bad%FF.png", "UTF-8"),
        ]:
            with self.subTest(filename=filename):
                if filename == "folder.png":
                    (self.root / filename).mkdir()
                elif filename == "large.png":
                    with (self.root / filename).open("wb") as stream:
                        stream.truncate(20 * 1024 * 1024 + 1)
                elif filename == "shot.txt":
                    self.asset(filename)
                result, _, err, factory = self.run_cli(self.doc(f"![alt]({filename})"))
                self.assertEqual(result, 2, err)
                self.assertIn(expected, err)
                factory.assert_not_called()

    def test_upload_basename_limit_counts_utf8_bytes(self):
        doc = self.doc("![alt](" + "界" * 301 + ".png)")
        result, _, err, factory = self.run_cli(doc)
        self.assertEqual(result, 2, err)
        self.assertIn("900 UTF-8 bytes", err)
        factory.assert_not_called()

    def test_symlink_cycle_is_a_planning_error(self):
        (self.root / "loop.png").symlink_to("loop.png")
        result, _, err, factory = self.run_cli(self.doc("![alt](loop.png)"))
        self.assertEqual(result, 2, err)
        self.assertIn("planning", err)
        self.assertIn("loop.png", err)
        self.assertIn("--images=alt", err)
        factory.assert_not_called()

    def test_uri_decodes_once_and_preserves_filename_characters(self):
        for filename, uri in [
            ("plus+hash#query?.PNG", "plus+hash%23query%3F.PNG"),
            ("percent%20.png", "percent%2520.png"),
            ("space name.png", "<space name.png>"),
            ("back\\slash.png", "back%5Cslash.png"),
        ]:
            with self.subTest(filename=filename):
                self.client.events.clear()
                self.asset(filename)
                result, _, err, _ = self.run_cli(
                    self.doc(f'![alt]({uri} "not a path")')
                )
                self.assertEqual(result, 0, err)
                created = next(p for s, p in self.client.events if s == "create_upload")
                self.assertEqual(created["filename"], filename)
                self.assertEqual(created["content_type"], "image/png")

    def test_alt_mode_does_not_read_assets(self):
        result, _, err, _ = self.run_cli(
            "--images=alt", self.doc("text ![**description**](nonexistent.png)")
        )
        self.assertEqual(result, 0, err)
        self.assertEqual([s for s, _ in self.client.events], ["page", "append"])
        self.assertIn("description", str(self.appended()))

    def test_dry_run_without_credentials_lists_assets_and_metadata(self):
        asset = self.asset()
        doc = self.doc("![a](shot.png)\n\n![b](shot.png)")
        result, _, err, factory = self.run_cli("--dry-run", doc, credentials=False)
        self.assertEqual(result, 0, err)
        factory.assert_not_called()
        for value in [
            str(asset),
            "image/png",
            str(asset.stat().st_size),
            "reuse",
            "No upload",
            "title=",
            "type=",
            "source=",
            "notes=",
            "lang=",
            "mode=markdown",
            doc,
        ]:
            self.assertIn(value, err)

    def test_code_paths_do_not_process_images(self):
        md = self.doc("![alt](missing.png)")
        code = self.doc("code", "code.py")
        for args, stdin in [
            (["--raw", md], b""),
            ([md, code], b""),
            ([md, "-", md], b"stdin"),
            (["--lang", "markdown"], b"![alt](missing.png)"),
        ]:
            with self.subTest(args=args):
                self.client.events.clear()
                result, _, err, _ = self.run_cli(*args, stdin=stdin)
                self.assertEqual(result, 0, err)
                self.assertEqual([s for s, _ in self.client.events], ["page", "append"])
                self.assertEqual([b["type"] for b in self.appended()], ["code"])
                expected = (
                    (Path(md).read_text() + "stdin" + Path(md).read_text())
                    if "-" in args
                    else None
                )
                if expected:
                    self.assertEqual(
                        self.appended()[0]["code"]["rich_text"][0]["text"]["content"],
                        expected,
                    )

    def test_fences_and_links_are_not_uploads(self):
        doc = self.doc("```md\n![alt](missing.png)\n```\n\n[ordinary](missing.png)")
        result, _, err, _ = self.run_cli(doc)
        self.assertEqual(result, 0, err)
        self.assertEqual([s for s, _ in self.client.events], ["page", "append"])

    def test_failure_stages_do_not_replay_or_leak_responses(self):
        self.asset()
        doc = self.doc("![alt](shot.png)")
        for stage, label, expected in [
            ("limit", "workspace-limit", ["limit"]),
            ("create_upload", "upload creation", ["limit", "create_upload"]),
            ("send", "binary send", ["limit", "create_upload", "send"]),
            ("page", "page creation", ["limit", "create_upload", "send", "page"]),
            (
                "append",
                "block append",
                ["limit", "create_upload", "send", "page", "append"],
            ),
        ]:
            with self.subTest(stage=stage):
                self.client.events.clear()
                self.client.failure = stage
                result, _, err, _ = self.run_cli(doc)
                self.assertEqual(result, 1, err)
                self.assertIn(label, err)
                self.assertNotIn("unsafe remote response", err)
                self.assertEqual([s for s, _ in self.client.events], expected)
                if stage in {"send", "page", "append"}:
                    self.assertIn("upload-1", err)
                if stage == "append":
                    self.assertIn("https://www.notion.so/pageid", err)

    def test_workspace_limit_is_required_and_enforced(self):
        self.asset(data=b"12345")
        for limit in [None, 0, -1, "200", True, 4]:
            with self.subTest(limit=limit):
                self.client.events.clear()
                self.client.limit = limit
                result, _, err, _ = self.run_cli(self.doc("![a](shot.png)"))
                self.assertEqual(result, 1, err)
                self.assertIn("workspace-limit", err)
                self.assertEqual([s for s, _ in self.client.events], ["limit"])
                if limit == 4:
                    self.assertIn("5", err)
                    self.assertIn("4", err)

    def test_changed_asset_is_checked_before_any_upload_creation(self):
        self.asset("first.png", b"first")
        changed = self.asset("second.png", b"small")
        self.client.limit = 10

        def retrieve_limit():
            response = self.client.me()
            changed.write_bytes(b"now larger than the workspace ceiling")
            return response

        self.client.users.me = retrieve_limit
        result, _, err, _ = self.run_cli(
            self.doc("![first](first.png)\n\n![second](second.png)")
        )
        self.assertEqual(result, 1, err)
        self.assertIn("ceiling 10", err)
        self.assertEqual([s for s, _ in self.client.events], ["limit"])

    def test_send_uses_size_checked_bytes_if_original_changes(self):
        asset = self.asset(data=b"checked image")
        self.client.limit = 20

        def create_upload(**kwargs):
            response = self.client.create_upload(**kwargs)
            asset.write_bytes(b"unchecked replacement" * 10)
            return response

        self.client.file_uploads.create = create_upload
        result, _, err, _ = self.run_cli(self.doc("![alt](shot.png)"))
        self.assertEqual(result, 0, err)
        self.assertEqual(
            [p["bytes"] for s, p in self.client.events if s == "send"],
            [b"checked image"],
        )

    def test_malformed_send_response_reports_binary_send_stage(self):
        self.asset()
        self.client.file_uploads.send = lambda **kwargs: []
        result, _, err, _ = self.run_cli(self.doc("![alt](shot.png)"))
        self.assertEqual(result, 1, err)
        self.assertIn("binary send", err)
        self.assertNotIn("upload creation:", err)
        self.assertIn("upload-1", err)
        self.assertNotIn("page", [s for s, _ in self.client.events])

    def test_uploaded_status_and_deadline_required_before_page(self):
        self.asset()
        for status, expiry in [
            ("pending", "2999-01-01T00:00:00Z"),
            ("uploaded", "2000-01-01T00:00:00Z"),
            ("uploaded", "invalid"),
        ]:
            with self.subTest(status=status, expiry=expiry):
                self.client.events.clear()
                self.client.upload_status, self.client.expiry = status, expiry
                result, _, err, _ = self.run_cli(self.doc("![alt](shot.png)"))
                self.assertEqual(result, 1, err)
                self.assertNotIn("page", [s for s, _ in self.client.events])
                self.assertIn("upload-1", err)


class UploadRetryTests(unittest.TestCase):
    def test_only_explicit_rejections_replay_binary_send(self):
        for status, code, attempts in [
            (429, "rate_limited", 3),
            (529, "service_overload", 3),
            (500, "internal_server_error", 1),
            (503, "service_unavailable", 1),
            (403, "restricted_resource", 1),
        ]:
            with self.subTest(status=status):
                bodies = []

                def respond(request, bodies=bodies, status=status, code=code):
                    self.assertEqual(
                        request.headers["Notion-Version"], cat.NOTION_VERSION
                    )
                    bodies.append(request.read())
                    return httpx.Response(
                        status,
                        json={
                            "object": "error",
                            "code": code,
                            "message": "private signed URL",
                        },
                        headers={"Retry-After": "0"},
                    )

                with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
                    client = cat.UploadClient(
                        auth="test",
                        client=transport,
                        notion_version=cat.NOTION_VERSION,
                        retry=RetryOptions(max_retries=2),
                    )
                    with (
                        patch("notion_client.client.time.sleep"),
                        self.assertRaises(HTTPResponseError),
                    ):
                        client.file_uploads.send(
                            file_upload_id="upload-1",
                            file=(
                                "shot.png",
                                io.BytesIO(b"full image body"),
                                "image/png",
                            ),
                        )
                self.assertEqual(len(bodies), attempts)
                self.assertTrue(all(b"full image body" in body for body in bodies))

    def test_timeout_is_not_retried(self):
        calls = []

        def respond(request):
            calls.append(request)
            raise httpx.ReadTimeout("ambiguous", request=request)

        with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
            client = cat.UploadClient(
                auth="test", client=transport, retry=RetryOptions(max_retries=2)
            )
            with self.assertRaises(RequestTimeoutError):
                client.file_uploads.create(
                    mode="single_part", filename="shot.png", content_type="image/png"
                )
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
