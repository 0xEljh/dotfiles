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
from urllib.parse import urlparse

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
    if any(p == "-" for p in paths):
        return DEFAULT_TYPE_OUTPUT
    if all(Path(p).suffix.lower() in {".md", ".mdx"} for p in paths):
        return DEFAULT_TYPE_DOC
    return DEFAULT_TYPE_OUTPUT


def infer_language(paths: list[str]) -> str:
    if not paths:
        return DEFAULT_LANGUAGE

    first: str | None = None
    for path in paths:
        if path == "-":
            return DEFAULT_LANGUAGE
        lang = EXT_TO_LANG.get(Path(path).suffix.lower())
        if lang is None:
            return DEFAULT_LANGUAGE
        if first is None:
            first = lang
        elif lang != first:
            return DEFAULT_LANGUAGE
    return first or DEFAULT_LANGUAGE


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
    return [
        {"type": "text", "text": {"content": text[i : i + MAX_RICH_TEXT_LEN]}}
        for i in range(0, len(text), MAX_RICH_TEXT_LEN)
    ]


def build_code_blocks(text: str, language: str) -> list[dict]:
    if text == "":
        return []
    notion_language = normalize_notion_language(language)
    items = chunk_rich_text(text)
    blocks = []
    for idx in range(0, len(items), MAX_RICH_TEXT_ITEMS):
        blocks.append(
            {
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": items[idx : idx + MAX_RICH_TEXT_ITEMS],
                    "language": notion_language,
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
    "jsonc": "json",
    "json5": "json",
    "text": "plain text",
    "plaintext": "plain text",
    "txt": "plain text",
    "cpp": "c++",
    "dockerfile": "docker",
    "md": "markdown",
    "jsx": "javascript",
    "tsx": "typescript",
}


def normalize_notion_language(language: str) -> str:
    lang_key = language.strip().lower()
    return NOTION_LANG_ALIASES.get(lang_key, lang_key or "plain text")


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


def _is_valid_notion_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return False
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.netloc)
    return bool(parsed.netloc or parsed.path)


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


def _inline_leaf(token: dict, annotations: dict) -> list[dict]:
    raw = token.get("raw", token.get("text", ""))
    if not raw:
        return []
    return [_notion_rich_text(raw, annotations or None)]


def _inline_recurse(token: dict, annotations: dict) -> list[dict]:
    items: list[dict] = []
    for child in token.get("children", []):
        items.extend(_inline_token_to_rich_text(child, annotations))
    return items


def _handle_inline_codespan(token: dict, ann: dict) -> list[dict]:
    return _inline_leaf(token, _merge_annotations(ann, {"code": True}))


def _handle_inline_strong(token: dict, ann: dict) -> list[dict]:
    return _inline_recurse(token, _merge_annotations(ann, {"bold": True}))


def _handle_inline_emphasis(token: dict, ann: dict) -> list[dict]:
    return _inline_recurse(token, _merge_annotations(ann, {"italic": True}))


def _handle_inline_strikethrough(token: dict, ann: dict) -> list[dict]:
    return _inline_recurse(token, _merge_annotations(ann, {"strikethrough": True}))


def _handle_inline_link(token: dict, ann: dict) -> list[dict]:
    url = (token.get("attrs") or {}).get("url") or token.get("link") or ""
    is_valid_link = _is_valid_notion_url(url)
    base_ann = dict(ann) if ann else {}
    items: list[dict] = []
    for child in token.get("children", []):
        child_items = _inline_token_to_rich_text(child, base_ann)
        if is_valid_link:
            for ci in child_items:
                ci["text"]["link"] = {"url": url}
        items.extend(child_items)
    if not items:
        raw = token.get("raw", url)
        rt = _notion_rich_text(raw, base_ann or None)
        if is_valid_link:
            rt["text"]["link"] = {"url": url}
        items.append(rt)
    return items


def _handle_inline_break(_token: dict, _ann: dict) -> list[dict]:
    return [_notion_rich_text("\n")]


def _handle_inline_image(token: dict, _ann: dict) -> list[dict]:
    alt = "".join(c.get("raw", c.get("text", "")) for c in token.get("children", []))
    return [_notion_rich_text(alt or "[image]")]


def _handle_inline_html(token: dict, _ann: dict) -> list[dict]:
    raw = token.get("raw", token.get("text", ""))
    return [_notion_rich_text(raw)] if raw else []


_INLINE_HANDLERS = {
    "text": _inline_leaf,
    "codespan": _handle_inline_codespan,
    "strong": _handle_inline_strong,
    "emphasis": _handle_inline_emphasis,
    "strikethrough": _handle_inline_strikethrough,
    "link": _handle_inline_link,
    "softbreak": _handle_inline_break,
    "linebreak": _handle_inline_break,
    "image": _handle_inline_image,
    "inline_html": _handle_inline_html,
}


def _inline_token_to_rich_text(token: dict, annotations: dict) -> list[dict]:
    handler = _INLINE_HANDLERS.get(token.get("type", ""))
    if handler is not None:
        return handler(token, annotations)
    return _inline_leaf(token, annotations)


def _make_block(block_type: str, rich_text: list[dict], **extra: object) -> dict:
    block: dict = {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": rich_text, **extra},
    }
    return block


def _row_cells_from_tokens(cell_tokens: list[dict]) -> list[list[dict]]:
    return [
        _ast_inline_to_rich_text(cell.get("children", [])) or [_notion_rich_text("")]
        for cell in cell_tokens
        if cell.get("type") == "table_cell"
    ]


def _table_to_block(token: dict) -> list[dict]:
    """Convert a mistune table token to a Notion table block with inline rows."""
    row_blocks: list[dict] = []
    has_column_header = False
    table_width = 0

    def _append_row(cells: list[list[dict]]) -> None:
        nonlocal table_width
        if not cells:
            return
        table_width = max(table_width, len(cells))
        row_blocks.append(
            {"object": "block", "type": "table_row", "table_row": {"cells": cells}}
        )

    for child in token.get("children", []):
        ct = child.get("type", "")
        if ct == "table_head":
            has_column_header = True
            _append_row(_row_cells_from_tokens(child.get("children", [])))
        elif ct == "table_body":
            for row_token in child.get("children", []):
                if row_token.get("type") == "table_row":
                    _append_row(_row_cells_from_tokens(row_token.get("children", [])))

    if not row_blocks:
        return []

    # Pad all rows to the same width
    for rb in row_blocks:
        cells = rb["table_row"]["cells"]
        while len(cells) < table_width:
            cells.append([_notion_rich_text("")])

    return [{
        "object": "block",
        "type": "table",
        "table": {
            "table_width": table_width,
            "has_column_header": has_column_header,
            "has_row_header": False,
            "children": row_blocks,
        },
    }]


def _ast_to_blocks(tokens: list[dict]) -> list[dict]:
    blocks: list[dict] = []
    for token in tokens:
        blocks.extend(_token_to_blocks(token))
    return blocks


_QUOTE_INLINE_PARENT_TYPES = frozenset(
    {"paragraph", "heading_1", "heading_2", "heading_3"}
)


def _handle_block_paragraph(token: dict) -> list[dict]:
    rt = _ast_inline_to_rich_text(token.get("children", []))
    return [_make_block("paragraph", rt)]


def _handle_block_heading(token: dict) -> list[dict]:
    level = min(token.get("attrs", {}).get("level", 1), 3)
    rt = _ast_inline_to_rich_text(token.get("children", []))
    return [_make_block(f"heading_{level}", rt)]


def _handle_block_code(token: dict) -> list[dict]:
    code = token.get("raw", token.get("text", ""))
    info = token.get("attrs", {}).get("info", "") or ""
    lang = info.split()[0] if info else ""
    notion_lang = normalize_notion_language(lang)
    rt = chunk_rich_text(code)
    return [
        _make_block("code", rt[i : i + MAX_RICH_TEXT_ITEMS], language=notion_lang)
        for i in range(0, len(rt), MAX_RICH_TEXT_ITEMS)
    ]


def _handle_block_quote(token: dict) -> list[dict]:
    child_blocks = _ast_to_blocks(token.get("children", []))
    if not child_blocks:
        return [_make_block("quote", [_notion_rich_text("")])]
    first = child_blocks[0]
    first_type = first.get("type", "")
    if first_type in _QUOTE_INLINE_PARENT_TYPES:
        rt = first[first_type]["rich_text"]
    else:
        rt = [_notion_rich_text("")]
    block = _make_block("quote", rt)
    if len(child_blocks) > 1:
        block["quote"]["children"] = child_blocks[1:]
    return [block]


def _handle_block_list(token: dict) -> list[dict]:
    ordered = token.get("attrs", {}).get("ordered", False)
    items: list[dict] = []
    for child in token.get("children", []):
        items.extend(_list_item_to_blocks(child, ordered))
    return items


def _handle_block_thematic_break(_token: dict) -> list[dict]:
    return [{"object": "block", "type": "divider", "divider": {}}]


def _handle_block_blank_line(_token: dict) -> list[dict]:
    return []


def _handle_block_html(token: dict) -> list[dict]:
    raw = token.get("raw", token.get("text", ""))
    if raw:
        return [_make_block("paragraph", [_notion_rich_text(raw)])]
    return []


_BLOCK_HANDLERS = {
    "paragraph": _handle_block_paragraph,
    "heading": _handle_block_heading,
    "block_code": _handle_block_code,
    "block_quote": _handle_block_quote,
    "list": _handle_block_list,
    "thematic_break": _handle_block_thematic_break,
    "table": _table_to_block,
    "blank_line": _handle_block_blank_line,
    "block_html": _handle_block_html,
    "block_text": _handle_block_paragraph,
}


def _token_to_blocks(token: dict) -> list[dict]:
    handler = _BLOCK_HANDLERS.get(token.get("type", ""))
    if handler is not None:
        return handler(token)
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
        if ct in {"paragraph", "block_text"} and not inline_rt:
            inline_rt = _ast_inline_to_rich_text(child.get("children", []))
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


def _is_retryable_error(exc: Exception) -> bool:
    status = getattr(exc, "status", None)
    if status in (403, 429):
        return True
    if isinstance(status, int) and status >= 500:
        return True

    code = getattr(exc, "code", None)
    if getattr(code, "value", code) == "rate_limited":
        return True
    return False


def _retry_with_backoff(fn, *, max_attempts: int = 5):
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # pragma: no cover
            attempt += 1
            if not _is_retryable_error(exc) or attempt >= max_attempts:
                raise
            time.sleep(1.5**attempt)


def create_page(
    notion: Client,
    data_source_id: str,
    properties: dict,
) -> dict:
    """Create a page in the specified data source."""
    parent = {"data_source_id": data_source_id}
    return _retry_with_backoff(
        lambda: notion.pages.create(parent=parent, properties=properties)
    )


def append_blocks(notion: Client, page_id: str, blocks: list[dict]) -> None:
    if not blocks:
        return
    for idx in range(0, len(blocks), MAX_CHILDREN_PER_REQUEST):
        batch = blocks[idx : idx + MAX_CHILDREN_PER_REQUEST]
        _retry_with_backoff(
            lambda b=batch: notion.blocks.children.append(block_id=page_id, children=b)
        )


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
    parser.add_argument(
        "--raw", action="store_true", help="Force code-block mode for .md/.mdx"
    )
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
    language = normalize_notion_language(
        args.lang or os.environ.get("NOTION_CAT_LANG") or inferred_lang
    )
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
