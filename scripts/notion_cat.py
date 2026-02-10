# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "mistune>=3.0",
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

import mistune
from dotenv import load_dotenv
from notion_client import Client


MAX_RICH_TEXT_LEN = 1999
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


NOTION_LANG_ALIASES = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "zsh": "bash",
    "shell": "bash",
    "yml": "yaml",
    "cpp": "c++",
    "dockerfile": "docker",
    "md": "markdown",
    "jsx": "javascript",
    "tsx": "typescript",
}


def _notion_rich_text(text: str, annotations: dict | None = None) -> dict:
    rt: dict = {"type": "text", "text": {"content": text}}
    if annotations:
        rt["annotations"] = annotations
    return rt


def _split_rich_text_item(item: dict) -> list[dict]:
    content = item["text"]["content"]
    if len(content) <= MAX_RICH_TEXT_LEN:
        return [item]
    parts = []
    for idx in range(0, len(content), MAX_RICH_TEXT_LEN):
        chunk = content[idx : idx + MAX_RICH_TEXT_LEN]
        new_item = {"type": "text", "text": {"content": chunk}}
        if "annotations" in item:
            new_item["annotations"] = item["annotations"]
        parts.append(new_item)
    return parts


def _ast_inline_to_rich_text(children: list[dict]) -> list[dict]:
    items: list[dict] = []
    for child in children:
        items.extend(_inline_token_to_rich_text(child, {}))
    split_items: list[dict] = []
    for item in items:
        split_items.extend(_split_rich_text_item(item))
    return split_items


def _merge_annotations(base: dict, override: dict) -> dict:
    merged = dict(base)
    merged.update(override)
    return merged


def _inline_token_to_rich_text(token: dict, annotations: dict) -> list[dict]:
    t = token.get("type", "")

    if t == "text":
        raw = token.get("raw", token.get("text", ""))
        if not raw:
            return []
        ann = annotations if annotations else None
        return [_notion_rich_text(raw, ann)]

    if t == "codespan":
        raw = token.get("raw", token.get("text", ""))
        if not raw:
            return []
        ann = _merge_annotations(annotations, {"code": True})
        return [_notion_rich_text(raw, ann)]

    if t == "strong":
        ann = _merge_annotations(annotations, {"bold": True})
        items: list[dict] = []
        for child in token.get("children", []):
            items.extend(_inline_token_to_rich_text(child, ann))
        return items

    if t == "emphasis":
        ann = _merge_annotations(annotations, {"italic": True})
        items = []
        for child in token.get("children", []):
            items.extend(_inline_token_to_rich_text(child, ann))
        return items

    if t == "strikethrough":
        ann = _merge_annotations(annotations, {"strikethrough": True})
        items = []
        for child in token.get("children", []):
            items.extend(_inline_token_to_rich_text(child, ann))
        return items

    if t == "link":
        url = ""
        if "attrs" in token:
            url = token["attrs"].get("url", "")
        elif "link" in token:
            url = token["link"]
        ann = dict(annotations) if annotations else {}
        items = []
        for child in token.get("children", []):
            child_items = _inline_token_to_rich_text(child, ann)
            for ci in child_items:
                ci["text"]["link"] = {"url": url}
            items.extend(child_items)
        if not items:
            raw = token.get("raw", url)
            rt = _notion_rich_text(raw, ann if ann else None)
            rt["text"]["link"] = {"url": url}
            items.append(rt)
        return items

    if t == "softbreak":
        return [_notion_rich_text("\n")]

    if t == "linebreak":
        return [_notion_rich_text("\n")]

    if t == "image":
        alt = ""
        if token.get("children"):
            for child in token["children"]:
                alt += child.get("raw", child.get("text", ""))
        return [_notion_rich_text(alt or "[image]")]

    if t == "inline_html":
        raw = token.get("raw", token.get("text", ""))
        return [_notion_rich_text(raw)] if raw else []

    raw = token.get("raw", token.get("text", ""))
    if raw:
        ann = annotations if annotations else None
        return [_notion_rich_text(raw, ann)]
    return []


def _make_block(block_type: str, rich_text: list[dict], **extra: object) -> dict:
    block: dict = {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": rich_text, **extra},
    }
    return block


def _ast_to_blocks(tokens: list[dict]) -> list[dict]:
    blocks: list[dict] = []
    for token in tokens:
        blocks.extend(_token_to_blocks(token))
    return blocks


def _token_to_blocks(token: dict) -> list[dict]:
    t = token.get("type", "")

    if t == "paragraph":
        rt = _ast_inline_to_rich_text(token.get("children", []))
        return [_make_block("paragraph", rt)]

    if t == "heading":
        level = token.get("attrs", {}).get("level", 1)
        if level > 3:
            level = 3
        block_type = f"heading_{level}"
        rt = _ast_inline_to_rich_text(token.get("children", []))
        return [_make_block(block_type, rt)]

    if t == "block_code":
        code = token.get("raw", token.get("text", ""))
        info = token.get("attrs", {}).get("info", "") or ""
        lang = info.split()[0] if info else ""
        lang_key = lang.lower()
        notion_lang = NOTION_LANG_ALIASES.get(lang_key, lang_key) or "plain text"
        rt = chunk_rich_text(code)
        result_blocks: list[dict] = []
        for idx in range(0, len(rt), MAX_RICH_TEXT_ITEMS):
            result_blocks.append(
                _make_block("code", rt[idx : idx + MAX_RICH_TEXT_ITEMS], language=notion_lang)
            )
        return result_blocks

    if t == "block_quote":
        child_blocks = _ast_to_blocks(token.get("children", []))
        if not child_blocks:
            return [_make_block("quote", [_notion_rich_text("")])]
        first = child_blocks[0]
        first_type = first.get("type", "")
        if first_type in ("paragraph", "heading_1", "heading_2", "heading_3"):
            rt = first[first_type]["rich_text"]
        else:
            rt = [_notion_rich_text("")]
        block = _make_block("quote", rt)
        if len(child_blocks) > 1:
            block["quote"]["children"] = child_blocks[1:]
        return [block]

    if t == "list":
        ordered = token.get("attrs", {}).get("ordered", False)
        items: list[dict] = []
        for child in token.get("children", []):
            items.extend(_list_item_to_blocks(child, ordered))
        return items

    if t == "thematic_break":
        return [{"object": "block", "type": "divider", "divider": {}}]

    if t == "blank_line":
        return []

    if t == "block_html":
        raw = token.get("raw", token.get("text", ""))
        if raw:
            return [_make_block("paragraph", [_notion_rich_text(raw)])]
        return []

    if t == "block_text":
        rt = _ast_inline_to_rich_text(token.get("children", []))
        return [_make_block("paragraph", rt)]

    raw = token.get("raw", token.get("text", ""))
    if raw:
        return [_make_block("paragraph", [_notion_rich_text(raw)])]
    return []


def _list_item_to_blocks(token: dict, ordered: bool) -> list[dict]:
    block_type = "numbered_list_item" if ordered else "bulleted_list_item"

    children = token.get("children", [])
    if not children:
        return [_make_block(block_type, [_notion_rich_text("")])]

    inline_rt: list[dict] = []
    nested_blocks: list[dict] = []

    for child in children:
        ct = child.get("type", "")
        if ct == "paragraph":
            if not inline_rt:
                inline_rt = _ast_inline_to_rich_text(child.get("children", []))
            else:
                nested_blocks.extend(_token_to_blocks(child))
        elif ct == "list":
            nested_blocks.extend(_token_to_blocks(child))
        elif ct == "block_text":
            if not inline_rt:
                inline_rt = _ast_inline_to_rich_text(child.get("children", []))
            else:
                nested_blocks.extend(_token_to_blocks(child))
        else:
            nested_blocks.extend(_token_to_blocks(child))

    if not inline_rt:
        inline_rt = [_notion_rich_text("")]

    block = _make_block(block_type, inline_rt)
    if nested_blocks:
        block[block_type]["children"] = nested_blocks
    return [block]


def build_markdown_blocks(text: str) -> list[dict]:
    if not text.strip():
        return []
    md = mistune.create_markdown(
        renderer=None,
        plugins=["strikethrough", "table"],
    )
    tokens = md(text)
    return _ast_to_blocks(tokens)


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
    parser.add_argument("--raw", action="store_true", help="Force code-block mode for .md/.mdx")
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
    use_markdown = inferred_lang == "markdown" and not args.raw
    mode = "markdown" if use_markdown else "code"

    if args.dry_run:
        eprint("dry-run: not creating Notion page")
        eprint(f"data_source_id={data_source_id}")
        eprint(f"title={title}")
        eprint(f"type={type_value}")
        eprint(f"source={source}")
        eprint(f"notes={notes}")
        eprint(f"lang={language}")
        eprint(f"mode={mode}")
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
    if use_markdown:
        blocks = build_markdown_blocks(text)
    else:
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
