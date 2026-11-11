# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "mistune>=3.0",
#     "notion-client==3.1.0",
#     "python-dotenv",
# ]
# ///

"""Publish Markdown files or literal stdin/code inputs to a new Notion page."""

from __future__ import annotations

import argparse
import logging
import os
import platform
import re
import socket
import stat
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO
from urllib.parse import unquote, urlparse, urlsplit

import mistune
from dotenv import load_dotenv
from notion_client import Client, RetryOptions

MAX_RICH_TEXT_LEN = 1999
MAX_RICH_TEXT_ITEMS = 100
MAX_CHILDREN_PER_REQUEST = 100
MAX_SINGLE_PART_BYTES = 20 * 1024 * 1024
NOTION_VERSION = "2025-09-03"
IMAGE_MIME_TYPES = {
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".ico": "image/vnd.microsoft.icon",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
    ".apng": "image/apng",
}

INLINE_CHILDREN_REQUIRED_TYPES = frozenset({"column", "column_list", "table", "tab"})

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


def file_title(path: str) -> str:
    file_path = Path(path)
    return file_path.stem.strip() or file_path.name.strip() or path


def default_title(paths: list[str], cwd_name: str, ts: str) -> str:
    if len(paths) == 1 and paths[0] != "-":
        return file_title(paths[0])
    if not paths or paths == ["-"]:
        return f"stdin capture - {cwd_name} - {ts}"
    if "-" in paths:
        return f"mixed input - {cwd_name} - {ts}"
    return f"{len(paths)} files - {cwd_name} - {ts}"


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
    "yml": "yaml",
    "jsonc": "json",
    "json5": "json",
    "jsonl": "json",
    "ndjson": "json",
    "text": "plain text",
    "plaintext": "plain text",
    "txt": "plain text",
    "cpp": "c++",
    "dockerfile": "docker",
    "md": "markdown",
    "jsx": "javascript",
    "tsx": "typescript",
}

# Authoritative list of languages accepted by Notion's code block API.
# Anything not in this set is rejected with a 400 validation error, so we
# fall back to "plain text" for unknown languages instead of trusting the
# raw fence info string.
NOTION_VALID_LANGUAGES = frozenset({
    "abap", "abc", "agda", "arduino", "ascii art", "assembly", "bash",
    "basic", "bnf", "c", "c#", "c++", "clojure", "coffeescript", "coq",
    "css", "dart", "dhall", "diff", "docker", "ebnf", "elixir", "elm",
    "erlang", "f#", "flow", "fortran", "gherkin", "glsl", "go", "graphql",
    "groovy", "haskell", "hcl", "html", "idris", "java", "javascript",
    "json", "julia", "kotlin", "latex", "less", "lisp", "livescript",
    "llvm ir", "lua", "makefile", "markdown", "markup", "matlab",
    "mathematica", "mermaid", "nix", "notion formula", "objective-c",
    "ocaml", "pascal", "perl", "php", "plain text", "powershell", "prolog",
    "protobuf", "purescript", "python", "r", "racket", "reason", "ruby",
    "rust", "sass", "scala", "scheme", "scss", "shell", "smalltalk",
    "solidity", "sql", "swift", "toml", "typescript", "vb.net", "verilog",
    "vhdl", "visual basic", "webassembly", "xml", "yaml",
    "java/c/c++/c#",
})


def normalize_notion_language(language: str) -> str:
    lang_key = language.strip().lower()
    mapped = NOTION_LANG_ALIASES.get(lang_key, lang_key or "plain text")
    if mapped not in NOTION_VALID_LANGUAGES:
        return "plain text"
    return mapped


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
    alt = _plain_inline_text(token.get("children", []))
    return [_notion_rich_text(alt or "[image]")]


def _plain_inline_text(tokens: list[dict]) -> str:
    parts = []
    for token in tokens:
        if "children" in token:
            parts.append(_plain_inline_text(token["children"]))
        elif token.get("type") in {"softbreak", "linebreak"}:
            parts.append("\n")
        else:
            parts.append(token.get("raw", token.get("text", "")))
    return "".join(parts)


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
    if not rt:
        return [_make_block("paragraph", [])]
    return [
        _make_block("paragraph", rt[i : i + MAX_RICH_TEXT_ITEMS])
        for i in range(0, len(rt), MAX_RICH_TEXT_ITEMS)
    ]


def _handle_block_heading(token: dict) -> list[dict]:
    level = min(token.get("attrs", {}).get("level", 1), 3)
    block_type = f"heading_{level}"
    rt = _ast_inline_to_rich_text(token.get("children", []))
    if not rt:
        return [_make_block(block_type, [])]
    return [
        _make_block(block_type, rt[i : i + MAX_RICH_TEXT_ITEMS])
        for i in range(0, len(rt), MAX_RICH_TEXT_ITEMS)
    ]


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
        child_blocks = child_blocks[1:]
    else:
        rt = [_notion_rich_text("")]
    block = _make_block("quote", rt)
    if child_blocks:
        block["quote"]["children"] = child_blocks
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
    "planned_image": lambda token: [token["block"]],
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


# Block types not allowed as children of list items by the Notion API.
# Promote these to siblings rather than nesting them.
_NON_NESTABLE_IN_LIST_ITEM = frozenset({"code", "table"})


def _list_item_to_blocks(token: dict, ordered: bool) -> list[dict]:
    block_type = "numbered_list_item" if ordered else "bulleted_list_item"

    children = token.get("children", [])
    if not children:
        return [_make_block(block_type, [_notion_rich_text("")])]

    inline_rt: list[dict] = []
    nested_blocks: list[dict] = []
    first_content = True

    for child in children:
        ct = child.get("type", "")
        if ct == "blank_line":
            continue
        if ct in {"paragraph", "block_text"} and first_content:
            inline_rt = _ast_inline_to_rich_text(child.get("children", []))
        else:
            nested_blocks.extend(_token_to_blocks(child))
        first_content = False

    if not inline_rt:
        inline_rt = [_notion_rich_text("")]

    nestable = [
        b for b in nested_blocks if b.get("type") not in _NON_NESTABLE_IN_LIST_ITEM
    ]
    promoted = [b for b in nested_blocks if b.get("type") in _NON_NESTABLE_IN_LIST_ITEM]

    block = _make_block(block_type, inline_rt[:MAX_RICH_TEXT_ITEMS])
    overflow_rt = inline_rt[MAX_RICH_TEXT_ITEMS:]
    if nestable:
        block[block_type]["children"] = nestable
    overflow_blocks = [
        _make_block("paragraph", overflow_rt[i : i + MAX_RICH_TEXT_ITEMS])
        for i in range(0, len(overflow_rt), MAX_RICH_TEXT_ITEMS)
    ]
    return [block] + overflow_blocks + promoted


@dataclass(frozen=True)
class MarkdownSource:
    text: str
    label: str
    base_dir: Path


@dataclass(frozen=True)
class ImageAsset:
    path: Path
    mime: str
    size: int


@dataclass
class ImageReference:
    asset: ImageAsset
    source: str
    placement: str
    block: dict


@dataclass
class PublishPlan:
    blocks: list[dict] = field(default_factory=list)
    assets: dict[Path, ImageAsset] = field(default_factory=dict)
    references: list[ImageReference] = field(default_factory=list)


def _local_image(url: str, base_dir: Path) -> ImageAsset:
    # Split before decoding: encoded '?' and '#' belong to filenames.
    parsed = urlsplit(url)
    if (
        not url
        or parsed.scheme
        or parsed.netloc
        or "?" in url
        or "#" in url
        or any(ord(char) < 32 for char in url)
    ):
        raise ValueError(
            "image source must be a local path without a scheme, authority, query, or fragment"
        )
    if re.search(r"%(?![0-9a-fA-F]{2})", parsed.path):
        raise ValueError("image path contains invalid percent encoding")
    try:
        decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError("image path must decode as UTF-8") from None
    if "\0" in decoded:
        raise ValueError("image path contains a NUL character")
    path = base_dir / decoded
    try:
        path = path.resolve()
    except RuntimeError as exc:
        # Python 3.11 reports symlink cycles as RuntimeError rather than OSError.
        raise ValueError(f"cannot resolve image {path}: {exc}") from None
    mime = IMAGE_MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"unsupported image extension: {path.suffix!r} ({path})")
    if len(path.name.encode("utf-8")) > 900:
        raise ValueError(f"upload basename exceeds 900 UTF-8 bytes: {path}")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"image must be a readable regular file: {path}")
        # Opening, without consuming bytes, verifies readability during preflight.
        with path.open("rb"):
            pass
    except FileNotFoundError:
        raise ValueError(f"missing image: {path}") from None
    if info.st_size > MAX_SINGLE_PART_BYTES:
        raise ValueError(
            f"image {path}: {info.st_size} bytes exceeds ceiling {MAX_SINGLE_PART_BYTES} bytes (single-part)"
        )
    return ImageAsset(path, mime, info.st_size)


def _image_tokens(token: dict):
    if token.get("type") == "image":
        yield token
    for child in token.get("children", []):
        yield from _image_tokens(child)


def build_publish_plan(sources: list[MarkdownSource], images: str) -> PublishPlan:
    plan = PublishPlan()
    md = mistune.create_markdown(renderer=None, plugins=["strikethrough", "table"])

    def convert(
        tokens: list[dict], source: MarkdownSource, ancestors: tuple[str, ...] = ()
    ) -> list[dict]:
        converted = []
        for token in tokens:
            kind = token.get("type", "")
            found = list(_image_tokens(token))
            if not found:
                converted.append(token)
                continue
            children = token.get("children", [])
            if kind in {"paragraph", "block_text"}:
                direct = [child for child in children if child.get("type") == "image"]
                whitespace_only = all(
                    child.get("type") == "image"
                    or child.get("type") in {"softbreak", "linebreak"}
                    or (
                        child.get("type") == "text" and not child.get("raw", "").strip()
                    )
                    for child in children
                )
                if not whitespace_only or len(direct) != len(found):
                    raise ValueError("move each image to its own image-only paragraph")
                for image in direct:
                    asset = _local_image(
                        image.get("attrs", {}).get("url", ""), source.base_dir
                    )
                    asset = plan.assets.setdefault(asset.path, asset)
                    caption = chunk_rich_text(
                        _plain_inline_text(image.get("children", []))
                    )
                    if len(caption) > MAX_RICH_TEXT_ITEMS:
                        raise ValueError("image caption exceeds 100 rich-text items")
                    block = {
                        "object": "block",
                        "type": "image",
                        "image": {
                            "type": "file_upload",
                            "file_upload": {},
                            "caption": caption,
                        },
                    }
                    placement = "/".join((*ancestors, kind))
                    plan.references.append(
                        ImageReference(asset, source.label, placement, block)
                    )
                    converted.append({"type": "planned_image", "block": block})
            elif kind in {"list", "list_item", "block_quote"}:
                token["children"] = convert(children, source, (*ancestors, kind))
                converted.append(token)
            else:
                raise ValueError(
                    f"images in {kind} are unsupported; move each image to its own paragraph"
                )
        return converted

    for source in sources:
        try:
            tokens = md(source.text)
            if images == "upload":
                tokens = convert(tokens, source)
            plan.blocks.extend(_ast_to_blocks(tokens))
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"{source.label}: {exc}. Use --images=alt for text-only publication."
            ) from None
    return plan


def report_plan(plan: PublishPlan, sources: list[MarkdownSource]) -> None:
    eprint("dry-run: local validation only. No upload or page creation occurred.")
    eprint("Workspace limits and server-side image acceptance are unchecked.")
    for source in sources:
        eprint(f"document={source.label}")
    seen: set[Path] = set()
    for reference in plan.references:
        asset = reference.asset
        action = "reuse" if asset.path in seen else "upload"
        eprint(
            f"asset: source={reference.source} path={asset.path} mime={asset.mime} bytes={asset.size} placement={reference.placement} action={action}"
        )
        seen.add(asset.path)


class PublicationError(Exception):
    pass


class UploadClient(Client):
    """One SDK retry layer, restricted to unambiguous upload rejections.

    These overrides target the pinned notion-client 3.1.0 retry hooks. Page and
    append calls use the ordinary client and retain their existing retry loops.
    """

    def _can_retry(self, error: Exception, method: str) -> bool:
        headers = getattr(error, "headers", {})
        delay = self._parse_retry_after_header(headers)
        # Stop rather than retry earlier than a long server-requested wait.
        if delay is not None and delay > self._max_retry_delay_ms:
            return False
        status = getattr(error, "status", None)
        code = getattr(error, "code", None)
        if status == 529 or (
            status == 429 and getattr(code, "value", code) == "rate_limited"
        ):
            return True
        return method.upper() == "GET" and super()._can_retry(error, method)

    def _calculate_retry_delay(self, error: Exception, attempt: int) -> float:
        delay = self._parse_retry_after_header(getattr(error, "headers", {}))
        if delay is not None:
            return delay / 1000
        return super()._calculate_retry_delay(error, attempt)

    def _log_request_error(self, error: Exception, attempt: int = 0) -> None:
        # The SDK's default warning includes the raw response message.
        pass


def _quiet_sdk_logger() -> logging.Logger:
    logger = logging.getLogger("notion-cat.sdk")
    logger.disabled = True
    return logger


def _remote_error(stage: str, exc: Exception) -> PublicationError:
    # API messages may contain signed URLs. Report only structured diagnostics.
    status = getattr(exc, "status", None)
    return PublicationError(
        f"{stage}: {type(exc).__name__}"
        + (f" (HTTP {status})" if isinstance(status, int) else "")
    )


def _upload_deadline(response: dict, stage: str) -> datetime | None:
    value = response.get("expiry_time")
    if value is None:
        return None
    try:
        deadline = datetime.fromisoformat(value)
        if deadline.tzinfo is None:
            raise ValueError
        return deadline
    except (ValueError, TypeError, AttributeError):
        raise PublicationError(f"{stage}: invalid expiry_time") from None


def _upload_response(response: object, stage: str) -> dict:
    if not isinstance(response, dict):
        raise PublicationError(f"{stage}: expected an upload response object")
    return response


def _snapshot_images(
    plan: PublishPlan, ceiling: int, cleanup: ExitStack
) -> dict[Path, BinaryIO]:
    """Freeze and check all bytes before the first remote mutation.

    Small snapshots stay in memory; larger ones spill to automatically removed
    temporary files. Read at most ceiling + 1 bytes even if an original grows.
    """
    snapshots = {}
    for asset in plan.assets.values():
        try:
            # ExitStack owns every snapshot until all uploads finish.
            snapshot = cleanup.enter_context(SpooledTemporaryFile(max_size=1024 * 1024))  # noqa: SIM115
            with asset.path.open("rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise PublicationError(
                        f"planning: image must be a regular file: {asset.path}"
                    )
                size = info.st_size
                copied = 0
                while size <= ceiling:
                    chunk = source.read(min(65536, ceiling + 1 - copied))
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > ceiling:
                        size = max(copied, os.fstat(source.fileno()).st_size)
                        break
                    snapshot.write(chunk)
                if size > ceiling:
                    raise PublicationError(
                        f"planning: image {asset.path}: {size} bytes exceeds ceiling {ceiling} bytes"
                    )
            snapshot.seek(0)
        except OSError as exc:
            raise PublicationError(
                f"planning: cannot snapshot {asset.path}: {exc}"
            ) from None
        snapshots[asset.path] = snapshot
    return snapshots


def upload_images(notion: Client, plan: PublishPlan, upload_ids: list[str]) -> None:
    if not plan.assets:
        return
    try:
        user = notion.users.me()
        limit = (
            user.get("bot", {})
            .get("workspace_limits", {})
            .get("max_file_upload_size_in_bytes")
        )
    except Exception as exc:  # noqa: BLE001 — sanitize errors at the SDK boundary
        raise _remote_error("workspace-limit retrieval", exc) from None
    if type(limit) is not int or limit <= 0:
        raise PublicationError(
            "workspace-limit retrieval: missing or invalid max_file_upload_size_in_bytes"
        )
    ceiling = min(limit, MAX_SINGLE_PART_BYTES)
    for asset in plan.assets.values():
        if asset.size > ceiling:
            raise PublicationError(
                f"workspace-limit retrieval: image {asset.path}: {asset.size} bytes exceeds ceiling {ceiling} bytes"
            )

    uploaded: dict[Path, str] = {}
    deadlines: list[datetime] = []
    with ExitStack() as cleanup:
        snapshots = _snapshot_images(plan, ceiling, cleanup)
        for asset in plan.assets.values():
            try:
                response = notion.file_uploads.create(
                    mode="single_part",
                    filename=asset.path.name,
                    content_type=asset.mime,
                )
            except Exception as exc:  # noqa: BLE001 — sanitize errors at the SDK boundary
                raise _remote_error("upload creation", exc) from None
            response = _upload_response(response, "upload creation")
            upload_id = response.get("id")
            if not isinstance(upload_id, str) or not upload_id:
                raise PublicationError("upload creation: no upload ID returned")
            upload_ids.append(upload_id)
            deadline = _upload_deadline(response, "upload creation")
            if deadline is not None:
                deadlines.append(deadline)
            try:
                response = notion.file_uploads.send(
                    file_upload_id=upload_id,
                    file=(asset.path.name, snapshots[asset.path], asset.mime),
                )
            except Exception as exc:  # noqa: BLE001 — report even ambiguous transport errors
                raise _remote_error(
                    "binary send (stopped; outcome may be ambiguous)", exc
                ) from None
            response = _upload_response(response, "binary send")
            if response.get("status") != "uploaded":
                raise PublicationError(
                    "binary send: upload did not reach uploaded status"
                )
            deadline = _upload_deadline(response, "binary send")
            if deadline is not None:
                deadlines.append(deadline)
            uploaded[asset.path] = upload_id

    for reference in plan.references:
        reference.block["image"]["file_upload"] = {"id": uploaded[reference.asset.path]}
    if any(deadline <= datetime.now(UTC) for deadline in deadlines):
        raise PublicationError(
            "page creation: an upload attachment deadline has elapsed; no page created"
        )


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


# Block types whose `children` must be supplied inline at creation time
# (Notion rejects empty tables/columns; rows/columns can't be appended later).
INLINE_CHILDREN_TYPES = {"table", "column_list", "column"}


def _pop_children_for_recursion(block: dict) -> list | None:
    block_type = block.get("type")
    if not block_type or block_type in INLINE_CHILDREN_TYPES:
        return None
    body = block.get(block_type)
    if not isinstance(body, dict):
        return None
    return body.pop("children", None)


def create_page(
    notion: Client,
    data_source_id: str,
    properties: dict,
) -> dict:
    parent = {"data_source_id": data_source_id}
    attempt = 0
    while True:
        try:
            return notion.pages.create(parent=parent, properties=properties)
        except Exception as exc:  # pragma: no cover
            attempt += 1
            if not _is_retryable_error(exc) or attempt > 4:
                raise
            time.sleep(1.5**attempt)


def append_blocks(notion: Client, page_id: str, blocks: list[dict]) -> None:
    if not blocks:
        return

    # Notion limits inline children nesting to 2 levels per request. Strip
    # children from each block and append them recursively to the created
    # block's id so arbitrarily deep trees (e.g. nested bullet lists) work.
    pending: list[list | None] = [_pop_children_for_recursion(b) for b in blocks]

    created_ids: list[str] = []
    for idx in range(0, len(blocks), MAX_CHILDREN_PER_REQUEST):
        batch = blocks[idx : idx + MAX_CHILDREN_PER_REQUEST]
        attempt = 0
        while True:
            try:
                resp = notion.blocks.children.append(block_id=page_id, children=batch)
                created_ids.extend(b.get("id", "") for b in resp.get("results", []))
                break
            except Exception as exc:  # pragma: no cover
                attempt += 1
                retryable = _is_retryable_error(exc)
                if not retryable or attempt > 4:
                    raise exc
                time.sleep(1.5**attempt)

    for created_id, children in zip(created_ids, pending):
        if children and created_id:
            append_blocks(notion, created_id, children)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Notion page from Markdown files or literal stdin/code inputs",
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
        "--suppress-output",
        action="store_true",
        help="Suppress the input echo and success messages except the page URL",
    )
    parser.add_argument(
        "--raw", action="store_true", help="Force code-block mode for .md/.mdx"
    )
    parser.add_argument(
        "--images",
        choices=("upload", "alt"),
        default="upload",
        help="Rendered Markdown images: upload local files (default), or descriptions only",
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

    data_source_id = args.data_source_id or os.environ.get("NOTION_CAT_DATA_SOURCE_ID")

    cwd_name = get_cwd_name()
    device = get_device_name()
    ts = timestamp_now()

    inferred_lang = infer_language(args.files)
    use_markdown = inferred_lang == "markdown" and not args.raw
    sources: list[MarkdownSource] = []
    try:
        if use_markdown:
            chunks = []
            for filename in args.files:
                path = Path(filename)
                chunk = path.read_bytes()
                chunks.append(chunk)
                sources.append(
                    MarkdownSource(
                        chunk.decode("utf-8", errors="replace"),
                        str(path),
                        path.absolute().parent,
                    )
                )
            data = b"".join(chunks)
            input_desc = (
                Path(args.files[0]).name
                if len(args.files) == 1
                else f"files:{len(args.files)}"
            )
        else:
            data, input_desc = read_all_inputs(args.files)
    except Exception as exc:
        eprint(f"planning: {exc}")
        return 2

    if not (args.no_stdout or args.suppress_output):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    inferred_type = infer_type_from_paths(args.files)

    title = (
        args.title
        or os.environ.get("NOTION_CAT_TITLE")
        or default_title(args.files, cwd_name, ts)
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
    mode = "markdown" if use_markdown else "code"

    try:
        plan = (
            build_publish_plan(sources, args.images)
            if use_markdown
            else PublishPlan(
                blocks=build_code_blocks(
                    data.decode("utf-8", errors="replace"), language
                )
            )
        )
    except (ValueError, OSError) as exc:
        eprint(f"planning: {exc}")
        return 2

    if args.dry_run:
        report_plan(plan, sources)
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

    token = os.environ.get("NOTION_TOKEN") or os.environ.get(
        "NOTION_TIME_ACCOUNTANT_SECRET"
    )
    if not token:
        eprint("missing NOTION_TOKEN (or NOTION_TIME_ACCOUNTANT_SECRET)")
        return 2
    if not data_source_id:
        eprint("missing NOTION_CAT_DATA_SOURCE_ID")
        return 2

    properties = build_properties(
        title=title, type_value=type_value, source=source, notes=notes
    )
    upload_ids: list[str] = []
    page_url: str | None = None
    clients: list[Client] = []
    stage = "upload creation"
    try:
        if plan.assets:
            uploader = UploadClient(
                auth=token,
                notion_version=NOTION_VERSION,
                retry=RetryOptions(max_retries=2),
                logger=_quiet_sdk_logger(),
            )
            clients.append(uploader)
            upload_images(uploader, plan, upload_ids)
        stage = "page creation"
        notion = Client(
            auth=token, notion_version=NOTION_VERSION, logger=_quiet_sdk_logger()
        )
        clients.append(notion)
        page = create_page(notion, data_source_id=data_source_id, properties=properties)
        page_id = page.get("id")
        if not page_id:
            raise PublicationError("page creation: no ID returned")
        page_url = (
            page.get("url") or f"https://www.notion.so/{page_id.replace('-', '')}"
        )
        stage = "block append"
        if plan.blocks:
            append_blocks(notion, page_id, plan.blocks)
        elif not args.suppress_output:
            eprint("note: empty input, no blocks appended")
    except Exception as exc:  # noqa: BLE001 — final CLI boundary; never expose API bodies
        error = exc if isinstance(exc, PublicationError) else _remote_error(stage, exc)
        eprint(str(error))
        if page_url:
            eprint(f"partial page: {page_url}")
        if upload_ids:
            eprint(f"known upload IDs: {', '.join(upload_ids)}")
            eprint(
                "Uploads may remain unattached or attached. No rollback or whole-command retry was performed."
            )
        return 1
    finally:
        for client in clients:
            client.close()

    eprint(f"created: {page_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
