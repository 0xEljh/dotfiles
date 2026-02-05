# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "notion-client",
#     "python-dotenv",
# ]
# ///

"""
notion-cat: create a new Notion page and append stdin/files as code blocks.
Uses Notion data source API (data_source_id) per latest Notion API.
"""

from __future__ import annotations

import argparse
import os
import platform
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from notion_client import Client


MAX_RICH_TEXT_LEN = 2000
MAX_RICH_TEXT_ITEMS = 100
MAX_CHILDREN_PER_REQUEST = 100

DEFAULT_TYPE_OUTPUT = "Output Log"
DEFAULT_TYPE_DOC = "Design Document"
DEFAULT_LANGUAGE = "plain text"

PROP_TITLE = "Title"
PROP_TYPE = "Type"
PROP_SOURCE = "Source"
PROP_NOTES = "Notes"

EXT_TO_LANG = {
    ".md": "markdown",
    ".mdx": "markdown",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".sh": "bash",
    ".zsh": "bash",
    ".bash": "bash",
    ".nix": "nix",
}


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def load_env_files(env_file: str | None) -> str | None:
    if env_file:
        env_path = Path(env_file).expanduser()
        if not env_path.exists():
            raise FileNotFoundError(f"env file not found: {env_path}")
        load_dotenv(env_path)
        return str(env_path)

    candidates = [
        os.environ.get("NOTION_CAT_ENV_FILE"),
        "~/.config/notion-cat/.env",
        "~/dotfiles/scripts/.env",
    ]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.exists():
            load_dotenv(path)
            return str(path)

    return None


def get_device_name() -> str:
    name = platform.node() or socket.gethostname()
    return name.strip() or "unknown-device"


def get_cwd_name() -> str:
    cwd = os.environ.get("PWD") or os.getcwd()
    name = Path(cwd).name
    return name if name else "root"


def timestamp_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def default_title(cwd_name: str, device: str, ts: str) -> str:
    return f"{cwd_name} @ {device} - {ts}"


def infer_type_from_paths(paths: list[str]) -> str:
    if not paths:
        return DEFAULT_TYPE_OUTPUT

    exts = []
    for path in paths:
        if path == "-":
            return DEFAULT_TYPE_OUTPUT
        exts.append(Path(path).suffix.lower())

    if exts and all(ext in {".md", ".mdx"} for ext in exts):
        return DEFAULT_TYPE_DOC
    return DEFAULT_TYPE_OUTPUT


def infer_language(paths: list[str]) -> str:
    if not paths:
        return DEFAULT_LANGUAGE

    langs = []
    for path in paths:
        if path == "-":
            return DEFAULT_LANGUAGE
        ext = Path(path).suffix.lower()
        langs.append(EXT_TO_LANG.get(ext))

    if langs and all(lang == langs[0] and lang is not None for lang in langs):
        return langs[0] or DEFAULT_LANGUAGE
    return DEFAULT_LANGUAGE


def read_all_inputs(paths: list[str]) -> tuple[bytes, str]:
    if not paths:
        if sys.stdin.isatty():
            raise ValueError("no input provided (stdin is a TTY)")
        data = sys.stdin.buffer.read()
        return data, "stdin"

    chunks: list[bytes] = []
    stdin_cache = b""
    stdin_loaded = False
    for path in paths:
        if path == "-":
            if not stdin_loaded:
                stdin_cache = sys.stdin.buffer.read()
                stdin_loaded = True
            chunks.append(stdin_cache)
            continue

        file_path = Path(path)
        try:
            chunks.append(file_path.read_bytes())
        except FileNotFoundError:
            raise FileNotFoundError(f"file not found: {file_path}")

    data = b"".join(chunks)
    if len(paths) == 1 and paths[0] != "-":
        return data, Path(paths[0]).name
    return data, f"files:{len(paths)}"


def chunk_rich_text(text: str) -> list[dict]:
    items = []
    for idx in range(0, len(text), MAX_RICH_TEXT_LEN):
        chunk = text[idx : idx + MAX_RICH_TEXT_LEN]
        items.append({"type": "text", "text": {"content": chunk}})
    return items


def build_code_blocks(text: str, language: str) -> list[dict]:
    if text == "":
        return []
    items = chunk_rich_text(text)
    blocks = []
    for idx in range(0, len(items), MAX_RICH_TEXT_ITEMS):
        blocks.append(
            {
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": items[idx : idx + MAX_RICH_TEXT_ITEMS],
                    "language": language,
                },
            }
        )
    return blocks


def build_properties(
    title: str,
    type_value: str,
    source: str,
    notes: str,
) -> dict:
    props: dict[str, dict] = {
        PROP_TITLE: {
            "title": [
                {"type": "text", "text": {"content": title}},
            ]
        },
        PROP_TYPE: {"select": {"name": type_value}},
        PROP_SOURCE: {
            "rich_text": [
                {"type": "text", "text": {"content": source}},
            ]
        },
    }

    if notes:
        props[PROP_NOTES] = {
            "rich_text": [
                {"type": "text", "text": {"content": notes}},
            ]
        }

    return props


def make_notes(cwd_name: str, input_desc: str, ts: str) -> str:
    return f"cwd={cwd_name}; input={input_desc}; captured={ts}"


def create_page(
    notion: Client,
    data_source_id: str,
    properties: dict,
) -> dict:
    """Create a page in the specified data source."""
    parent = {"data_source_id": data_source_id}
    return notion.pages.create(parent=parent, properties=properties)


def append_blocks(notion: Client, page_id: str, blocks: list[dict]) -> None:
    if not blocks:
        return
    for idx in range(0, len(blocks), MAX_CHILDREN_PER_REQUEST):
        batch = blocks[idx : idx + MAX_CHILDREN_PER_REQUEST]
        attempt = 0
        while True:
            try:
                notion.blocks.children.append(block_id=page_id, children=batch)
                break
            except Exception as exc:  # pragma: no cover
                attempt += 1
                if attempt > 4:
                    raise exc
                time.sleep(1.5**attempt)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Notion page and append stdin/files as code blocks",
    )
    parser.add_argument("files", nargs="*", help="Files to append. Use '-' for stdin")
    parser.add_argument("--title", help="Page title")
    parser.add_argument("--type", help="Type property value")
    parser.add_argument("--source", help="Source property value")
    parser.add_argument("--notes", help="Notes property value")
    parser.add_argument("--lang", help="Code block language")
    parser.add_argument("--data-source-id", help="Notion data source id")
    parser.add_argument("--env-file", help="Path to .env file")
    parser.add_argument("--no-stdout", action="store_true", help="Do not echo input")
    parser.add_argument("--dry-run", action="store_true", help="Do not call Notion")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        env_loaded = load_env_files(args.env_file)
    except FileNotFoundError as exc:
        eprint(str(exc))
        return 2

    token = os.environ.get("NOTION_TOKEN") or os.environ.get(
        "NOTION_TIME_ACCOUNTANT_SECRET"
    )
    data_source_id = args.data_source_id or os.environ.get("NOTION_CAT_DATA_SOURCE_ID")

    if not token:
        eprint("missing NOTION_TOKEN (or NOTION_TIME_ACCOUNTANT_SECRET)")
        return 2
    if not data_source_id:
        eprint("missing NOTION_CAT_DATA_SOURCE_ID")
        return 2

    cwd_name = get_cwd_name()
    device = get_device_name()
    ts = timestamp_now()

    try:
        data, input_desc = read_all_inputs(args.files)
    except Exception as exc:
        eprint(str(exc))
        return 2

    if not args.no_stdout:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    inferred_type = infer_type_from_paths(args.files)
    inferred_lang = infer_language(args.files)

    title = (
        args.title
        or os.environ.get("NOTION_CAT_TITLE")
        or default_title(cwd_name, device, ts)
    )
    type_value = args.type or os.environ.get("NOTION_CAT_TYPE") or inferred_type
    source = args.source or os.environ.get("NOTION_CAT_SOURCE") or device
    notes = (
        args.notes
        if args.notes is not None
        else os.environ.get("NOTION_CAT_NOTES") or make_notes(cwd_name, input_desc, ts)
    )
    language = args.lang or os.environ.get("NOTION_CAT_LANG") or inferred_lang

    if args.dry_run:
        eprint("dry-run: not creating Notion page")
        eprint(f"data_source_id={data_source_id}")
        eprint(f"title={title}")
        eprint(f"type={type_value}")
        eprint(f"source={source}")
        eprint(f"notes={notes}")
        eprint(f"lang={language}")
        if env_loaded:
            eprint(f"env={env_loaded}")
        return 0

    notion = Client(auth=token)

    properties = build_properties(
        title=title, type_value=type_value, source=source, notes=notes
    )
    page = create_page(
        notion,
        data_source_id=data_source_id,
        properties=properties,
    )
    page_id = page.get("id")
    if not page_id:
        eprint("failed to create page (no id returned)")
        return 1

    text = data.decode("utf-8", errors="replace")
    blocks = build_code_blocks(text, language)
    if blocks:
        append_blocks(notion, page_id, blocks)
    else:
        eprint("note: empty input, no blocks appended")

    short_id = page_id.replace("-", "")
    page_url = f"https://www.notion.so/{short_id}"
    eprint(f"created: {page_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
